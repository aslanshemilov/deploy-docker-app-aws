#!/usr/bin/env python3

#
# Modules Import
#
import argparse, boto3, glob, json, os, shlex, shutil, subprocess, sys, time
from zipfile import ZipFile

#
# Variables Definition
#
build_path = './build'
application_path = build_path + '/libs'
elasticbeanstalk_files_list = ['./Dockerrun.aws.json', './Dockerfile', 'env.yaml']
source_bundle_s3_bucket_prefix = 'elasticbeanstalk'

#
# Function to parse the input arguments and build the help message
#
def arguments_parser():
    parser = argparse.ArgumentParser(description='Custom CLI to deploy an application to AWS Elastic Beanstalk', add_help=True)

    options = parser.add_argument_group('Options')
    options.add_argument('-a', '--application-name', type=str, action='store', dest='application_name', required=True, help='Name of the Elastic Beanstalk application')
    options.add_argument('-e', '--environment-name', type=str, action='store', dest='environment_name', required=True, help='Name of the deployment environment for the Elastic Beanstalk application')
    options.add_argument('-p', '--profile', type=str, action='store', dest='profile', default='default', help='Use a specific profile from AWS CLI stored configurations')

    args = parser.parse_args()
    return args

#
# Function to print the result of system commands executions
#
def print_result(output, error):
    if output != '':
        print(output)
    if error != '':
        print(error)

#
# Function to print the boto3 responses in JSON format
#
def print_response(response):
    print(json.dumps(response, default=str, sort_keys=True, indent=4, separators=(',', ': ')))

#
# Function to create a session of boto3 to interact with the AWS account
#
def create_session():
    profile = arguments.profile
    if (profile != 'default') and (not profile in boto3.session.Session().available_profiles):
        print("\nThe '" + profile + "' profile does not exist!\n")
    elif (profile == 'default') and (boto3.session.Session().get_credentials() is None):
        print("\nThere is no AWS CLI configuration defined!\n")
    elif profile != 'default':
        return boto3.session.Session(profile_name=profile)
    else:
        return boto3.session.Session()

    #print("Please provide AWS configuration, e.g. via the AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY and AWS_DEFAULT_REGION environment variables\n")
    #exit(-1)
    access_key = input("Enter the AWS_ACCESS_KEY_ID of the AWS account in which to deploy the application: ")
    secret_access_key = input("Enter the AWS_SECRET_ACCESS_KEY of the AWS account in which to deploy the application: ")
    region = input("Enter the region in which to deploy the application: ")

    return boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_access_key,
        region_name=region,
    )

#
# Function to build the application
#
def build_application():
    shutil.rmtree(build_path, ignore_errors=True)
    os.chdir(os.path.dirname(__file__))
    gradlew_build_command = shlex.split('./gradlew build')
    output, error = subprocess.Popen(gradlew_build_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8').communicate()
    print_result(output, error)

#
# Function to create the ZIP package for the new application version
#
def package_application():
    app_file = glob.glob(application_path + '/*.jar')[0]
    elasticbeanstalk_files_list.append(app_file)
    app_name = os.path.splitext(os.path.basename(app_file))[0]
    app_ver_pkg_name = app_name + '-' + time.strftime('%Y%m%d-%H%M%S')
    with ZipFile(app_ver_pkg_name + '.zip', 'w') as zip:
        for file in elasticbeanstalk_files_list:
            zip.write(file)
    print("All application files zipped successfully to '" + app_ver_pkg_name + ".zip' package\n")
    return app_ver_pkg_name

#
# Function to upload an application version package to a source bundle S3 bucket
#
def upload_application_version():
    sts = session.client('sts')
    s3_bucket = source_bundle_s3_bucket_prefix + '-' + session.region_name + '-' + sts.get_caller_identity()['Account']
    s3 = session.resource('s3')
    if not s3.Bucket(s3_bucket) in s3.buckets.all():
        s3.create_bucket(
            ACL='private',
            Bucket=s3_bucket,
            CreateBucketConfiguration={'LocationConstraint': session.region_name}
        )
    response = s3.Bucket(s3_bucket).put_object(
        Body=open(application_version_package_name + '.zip', 'rb'),
        Key=application_version_package_name + '.zip'
    )
    print(response)
    return s3_bucket

#
# Main
#

# Change directory to the project root folder
os.chdir(os.path.dirname(__file__))

# Parse input arguments
arguments = arguments_parser()

session = create_session()
elasticbeanstalk = session.client('elasticbeanstalk')

# Check if the Elastic Beanstalk application is already created
application_name = arguments.application_name
if not elasticbeanstalk.describe_applications(ApplicationNames=[application_name])['Applications']:
    print("\nCreate '" + application_name + "' Elastic Beanstalk application:\n")
    response = elasticbeanstalk.create_application(
        ApplicationName=application_name,
        Description=application_name + ' Application'
    )
    print_response(response)

print("\nBuild the application:\n")
build_application()

print("\nCreate the ZIP package for the new application version:\n")
application_version_package_name = package_application()

print("\nUpload the application version package to a source bundle S3 bucket:\n")
source_bundle_s3_bucket = upload_application_version()

print("\nCreate '" + application_version_package_name + "' application version for '" + application_name + "' Elastic Beanstalk application:\n")
response = elasticbeanstalk.create_application_version(
    ApplicationName=application_name,
    VersionLabel=application_version_package_name,
    SourceBundle={
        'S3Bucket': source_bundle_s3_bucket,
        'S3Key': application_version_package_name + '.zip'
    },
    AutoCreateApplication=False,
    Process=True
)
print_response(response)

# Wait for a seconds while the created application version is processed
time.sleep(10)

environment_name = application_name + '-' + arguments.environment_name

# Check if there is an operation in progress in the environment and wait for it to be completed before updating or recreating it
environment_configuration = elasticbeanstalk.describe_environments(ApplicationName=application_name, EnvironmentNames=[environment_name])['Environments']
wait_message_printed = False
while (environment_configuration) and (environment_configuration[0]['Status'] != 'Ready') and (environment_configuration[0]['Status'] != 'Terminated'):
    if not wait_message_printed:
        print("\nThere is an operation in progress in '" + environment_name + "' environment. Waiting for it to be completed...\n")
        wait_message_printed = True
    time.sleep(30)
    environment_configuration = elasticbeanstalk.describe_environments(ApplicationName=application_name, EnvironmentNames=[environment_name])['Environments']

if (not environment_configuration) or (environment_configuration[0]['Status'] == 'Terminated'):
    print("\nCreate '" + environment_name + "' deployment environment for '" + application_name + "' Elastic Beanstalk application:\n")
    response = elasticbeanstalk.create_environment(
        ApplicationName=application_name,
        EnvironmentName=environment_name,
        Description=environment_name + ' Environment',
        CNAMEPrefix=environment_name,
        VersionLabel=application_version_package_name
    )
else:
    print("\nUpdate '" + environment_name + "' deployment environment for '" + application_name + "' Elastic Beanstalk application:\n")
    response = elasticbeanstalk.update_environment(
        ApplicationName=application_name,
        EnvironmentName=environment_name,
        VersionLabel=application_version_package_name
    )
print_response(response)
