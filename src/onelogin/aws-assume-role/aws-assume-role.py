#!/usr/bin/python

import base64
import getpass
import json
import os
import sys
import time
from argparse import ArgumentParser

import boto3
import botocore
from botocore.exceptions import ClientError
from lxml import etree as ET
from onelogin.api.client import OneLoginClient

from writer import ConfigFileWriter


MFA_ATTEMPS_FOR_WARNING = 3
TIME_SLEEP_ON_RESPONSE_PENDING = 30
MAX_ITER_GET_SAML_RESPONSE = 10

def get_options():
    parser = ArgumentParser()

    parser.add_argument("-i", "--client_id", dest="client_id",
                      help="A valid OneLogin API client_id")
    parser.add_argument("-s", "--client_secret", dest="client_secret",
                      help="A valid OneLogin API client_secret")
    parser.add_argument("-r", "--region", dest="region", default="us",
                      help="Onelogin region. us or eu  (Default value: us)")

    parser.add_argument("-t", "--time", dest="time", default=45, type=int,
                      help="Sleep time between iterations, in minutes  [15-60 min]")
    parser.add_argument("-l", "--loop", dest="loop", default=1, type=int,
                      help="Number of iterations")
    parser.add_argument("-p", "--profile", dest="profile_name",
                      help="Save Temporal AWS credentials using that profile name")
    parser.add_argument("-f", "--file", dest="file", 
                      help="Set a custom path to save the AWS credentials. (if not used, default AWS path is used)")

    parser.add_argument("-u", "--onelogin-username", dest="username",
                      help="OneLogin username (email address)")
    parser.add_argument("-a", "--onelogin-app-id", dest="app_id",
                      help="OneLogin app id")
    parser.add_argument("-d", "--onelogin-subdomain", dest="subdomain",
                      help="OneLogin subdomain")
    parser.add_argument("--aws-region", dest="aws_region",
                      help="AWS region to use")

    options = parser.parse_args()

    options.time = options.time
    if options.time < 15:
        options.time = 15
    elif options.time > 60:
        options.time = 60

    return options


def get_client(options):
    client_id = client_secret = None

    if options.client_id is not None and options.client_secret is not None:
        client_id = options.client_id
        client_secret = options.client_secret
        region = options.region
    else:
        if os.path.isfile('onelogin.sdk.json'):
            json_data = open('onelogin.sdk.json').read()
            data = json.loads(json_data)
            if 'client_id' in data.keys() and 'client_secret' in data.keys():
                client_id = data['client_id']
                client_secret = data['client_secret']
                region = data.get('region', 'us')
                ip = data.get('ip', None)

    if client_id is None or client_secret is None:
        raise Exception("OneLogin Client ID and Secret are required")

    client = OneLoginClient(client_id, client_secret, region)
    if ip:
        client.ip = ip
    client.prepare_token()
    if client.error == 401 or client.access_token is None:
        raise Exception("Invalid client_id and client_secret. Access_token could not be retrieved")
    return client


def check_device_exists(devices, device_id):
    for device in devices:
        if device.id == device_id:
            return True
    return False


def get_saml_response(client, username_or_email, password, app_id, onelogin_subdomain, ip=None, mfa_verify_info=None):
    saml_endpoint_response = client.get_saml_assertion(username_or_email, password, app_id, onelogin_subdomain, ip)

    try_get_saml_response = 0
    while saml_endpoint_response is None or saml_endpoint_response.type == "pending":
        if saml_endpoint_response is None:
            if client.error in ['400', '401']:
                error_msg = "\n\nError %s. %s" % (client.error, client.error_description)
                if client.error_description == "Invalid subdomain":
                    print(error_msg)
                    print("\nOnelogin Instance Sub Domain: ")
                    onelogin_subdomain = sys.stdin.readline().strip()
                elif client.error_description in ["Authentication Failed: Invalid user credentials",
                    "password is empty"]:
                    print(error_msg)
                    password = getpass.getpass("\nOneLogin Password: ")
                elif client.error_description == "username is empty":
                    print(error_msg)
                    print("OneLogin Username: ")
                    username_or_email = sys.stdin.readline().strip()
                else:
                    raise Exception(error_msg)

        if saml_endpoint_response and saml_endpoint_response.type == "pending":
            time.sleep(TIME_SLEEP_ON_RESPONSE_PENDING)
        saml_endpoint_response = client.get_saml_assertion(username_or_email, password, app_id, onelogin_subdomain, ip)
        try_get_saml_response += 1
        if try_get_saml_response == MAX_ITER_GET_SAML_RESPONSE:
            print("Not able to get a SAMLResponse with success status after %s iteration(s)." % MAX_ITER_GET_SAML_RESPONSE)
            sys.exit()

    if saml_endpoint_response and saml_endpoint_response.type == "success":
        if saml_endpoint_response.mfa is not None:
            mfa = saml_endpoint_response.mfa
            devices = mfa.devices

            if mfa_verify_info is None:
                print("\nMFA Required")
                print("Authenticate using one of these devices:")
            else:
                device_id = mfa_verify_info['device_id']
                if not check_device_exists(devices, device_id):
                    print("\nThe device selected with ID %s is not available anymore" % device_id)
                    print("Those are the devices available now:")
                    mfa_verify_info = None

            if mfa_verify_info is None:
                print("-----------------------------------------------------------------------")
                for index, device in enumerate(devices):
                    print(" " + str(index) + " | " + device.type)

                print("-----------------------------------------------------------------------")

                if len(devices) > 1:
                    print("\nSelect the desired MFA Device [0-%s]: " % (len(devices) - 1))
                    device_selection = int(sys.stdin.readline().strip())
                else:
                    device_selection = 0
                device = devices[device_selection]
                device_id = device.id

                print("Enter the OTP Token for %s: " % device.type)
                otp_token = sys.stdin.readline().strip()
                state_token = mfa.state_token
                mfa_verify_info = {
                    'otp_token': otp_token,
                    'state_token': state_token,
                    'device_id': device_id
                }
            else:
                otp_token = mfa_verify_info['otp_token']
                state_token = mfa_verify_info['state_token']

            saml_endpoint_response = client.get_saml_assertion_verifying(app_id, device_id, state_token, otp_token)
            mfa_error = 0
            while client.error_description == "Failed authentication with this factor":
                if mfa_error > MFA_ATTEMPS_FOR_WARNING and len(devices) > 1:
                    print("The OTP Token was invalid again, Do you want to select a new MFA method? (y/n)")
                    answer = get_yes_or_not()
                    if answer == 'y':
                        # Let's regenerate the SAMLResponse and initialize again the count
                        print("\n");
                        return get_saml_response(client, username_or_email, password, app_id, onelogin_subdomain, ip, None)
                    else:
                        print("Ok, Try introduce a new OTP Token then: ")
                else:
                    print("The OTP Token was invalid or expired, please introduce a new one: ")

                otp_token = sys.stdin.readline().strip()
                saml_endpoint_response = client.get_saml_assertion_verifying(app_id, device_id, state_token, otp_token)
                mfa_verify_info['otp_token'] = otp_token
                mfa_error = mfa_error + 1

            if saml_endpoint_response is None:
                print("There was an issue with the MFA validation, restarting the process")
                return get_saml_response(client, username_or_email, password, app_id, onelogin_subdomain, ip, mfa_verify_info)

        saml_response = saml_endpoint_response.saml_response

    result = {
        'saml_response': saml_response,
        'mfa_verify_info': mfa_verify_info,
        'username_or_email': username_or_email,
        'password': password,
        'onelogin_subdomain': onelogin_subdomain,
    }
    return result


def get_attributes(saml_response):
    saml_response_xml = base64.b64decode(saml_response)
    saml_response_elem = ET.fromstring(saml_response_xml)
    NSMAP = {
        'samlp': 'urn:oasis:names:tc:SAML:2.0:protocol',
        'saml': 'urn:oasis:names:tc:SAML:2.0:assertion'
    }
    attributes = {}
    attribute_nodes = saml_response_elem.xpath('//saml:AttributeStatement/saml:Attribute', namespaces=NSMAP)
    for attribute_node in attribute_nodes:
        attr_name = attribute_node.get('Name')
        values = []
        for attr in attribute_node.iterchildren('{%s}AttributeValue' % NSMAP['saml']):
            values.append(element_text(attr))
        attributes[attr_name] = values
    return attributes


def element_text(node):
    ET.strip_tags(node, ET.Comment)
    return node.text

def get_yes_or_not():
    answer = None
    while (answer != 'y' and answer != 'n'):
        answer = sys.stdin.readline().strip().lower()
    return answer

def main():
    print("\nOneLogin AWS Assume Role Tool\n")

    options = get_options()

    client = get_client(options)

    client.get_access_token()

    mfa_verify_info = None
    role_arn = principal_arn = None
    default_aws_region = 'us-west-2'
    ip = None

    if hasattr(client, 'ip'):
        ip = client.ip

    config_file_writer = None
    botocore_config = botocore.client.Config(signature_version=botocore.UNSIGNED)
    loops = options.loop
    exception = False
    ask_for_user_again = False
    ask_for_role_again = False
    i = 0;
    while (i < loops):
        if ask_for_user_again:
            print("OneLogin Username: ")
            username_or_email = sys.stdin.readline().strip()

            password = getpass.getpass("\nOneLogin Password: ")
            ask_for_role_again = True
        else:
            if i == 0:
                # Capture OneLogin Account Details
                if options.username:
                    username_or_email = options.username
                else:
                    print("OneLogin Username: ")
                    username_or_email = sys.stdin.readline().strip()

                password = getpass.getpass("\nOneLogin Password: ")

                if options.app_id:
                    app_id = options.app_id
                else:
                    print("\nAWS App ID: ")
                    app_id = sys.stdin.readline().strip()

                if options.subdomain:
                    onelogin_subdomain = options.subdomain
                else:
                    print("\nOnelogin Instance Sub Domain: ")
                    onelogin_subdomain = sys.stdin.readline().strip()
            elif exception:
                exception = False
            else:
                time.sleep(options.time * 60)

        result = get_saml_response(client, username_or_email, password, app_id, onelogin_subdomain, ip, mfa_verify_info)

        mfa_verify_info = result['mfa_verify_info']
        saml_response = result['saml_response']
        username_or_email = result['username_or_email']
        password = result['password']
        onelogin_subdomain = result['onelogin_subdomain']

        if i == 0 or ask_for_role_again:
            attributes = get_attributes(saml_response)
            if 'https://aws.amazon.com/SAML/Attributes/Role' not in attributes.keys():
                print("SAMLResponse from Identity Provider does not contain AWS Role info")

                print("Do you want to select a new user?  (y/n)")
                answer = get_yes_or_not()
                if answer == 'y':
                    ask_for_user_again = True
                    i = i + 1
                    loops = loops + 1
                    continue
                else:
                    sys.exit()
            else:
                roles = attributes['https://aws.amazon.com/SAML/Attributes/Role']
                selected_role = None
                if len(roles) > 1:
                    print("\nAvailable AWS Roles")
                    print("-----------------------------------------------------------------------")
                    for index, role in enumerate(roles):
                        role_info = role.split(":")
                        account_id = role_info[4]
                        role_name = role_info[5].replace("role/", "")
                        print(" %s | %s (Account %s)" % (index, role_name, account_id))
                    print("-----------------------------------------------------------------------")
                    print("Select the desired Role [0-%s]: " % (len(roles) - 1))
                    selected_role = roles[int(sys.stdin.readline().strip())]
                elif len(roles) == 1:
                    selected_role = roles[0]
                else:
                    print("SAMLResponse from Identity Provider does not contain available AWS Role for this user")

                    print("Do you want to select a new user?  (y/n)")
                    answer = get_yes_or_not()
                    if answer == 'y':
                        ask_for_user_again = True
                        i = i + 1
                        loops = loops + 1
                        continue
                    else:
                        sys.exit()

                if selected_role is not None:
                    selected_role_data = selected_role.split(',')
                    role_arn = selected_role_data[0]
                    principal_arn = selected_role_data[1]

        if i == 0:
            # AWS Region
            if options.aws_region:
                aws_region = options.aws_region
            else:
                print("\nAWS Region (" + default_aws_region + "): ")
                aws_region = sys.stdin.readline().strip()
            if not aws_region or aws_region == "-":
                aws_region = default_aws_region

        conn = boto3.client('sts', region_name=aws_region, config=botocore_config)
        try:
            aws_session_token = conn.assume_role_with_saml(
                RoleArn=role_arn,
                PrincipalArn=principal_arn,
                SAMLAssertion=saml_response,
                DurationSeconds=3600
            )
        except ClientError as err:
            if 'Token must be redeemed within 5 minutes of issuance' in err.message:
                print err.message
                print "Generating a new SAMLResponse with the data already provided...."
                exception = True
                i = i + 1
                loops = loops +1
                continue
            else:
                raise err

        i = i + 1
        ask_for_user_again = ask_for_role_again = False

        access_key_id = aws_session_token['Credentials']['AccessKeyId']
        secret_access_key = aws_session_token['Credentials']['SecretAccessKey']
        session_token = aws_session_token['Credentials']['SessionToken']
        arn = aws_session_token['AssumedRoleUser']['Arn']

        if options.profile_name is None and options.file is None:
            action = "export"
            if sys.platform.startswith('win'):
                action = "set"

            print("\n-----------------------------------------------------------------------\n")
            print("Success!\n")
            print("Assumed Role User: %s\n" % arn)
            print("Temporary AWS Credentials Granted via OneLogin\n")
            print("Copy/Paste to set these as environment variables\n")
            print("-----------------------------------------------------------------------\n")

            print("%s AWS_SESSION_TOKEN=%s\n" % (action, session_token))
            print("%s AWS_ACCESS_KEY_ID=%s\n" % (action, access_key_id))
            print("%s AWS_SECRET_ACCESS_KEY=%s\n" % (action, secret_access_key))
        else:
            if options.file is None:
                options.file = os.path.expanduser('~/.aws/credentials')

            if options.profile_name is None:
                options.profile_name = "default"

            if config_file_writer is None:
                config_file_writer = ConfigFileWriter()

            updated_config = {
                '__section__': options.profile_name,
                'aws_access_key_id': access_key_id,
                'aws_secret_access_key': secret_access_key,
                'aws_session_token': session_token,
            }
            config_file_writer.update_config(updated_config, options.file)

            print("Success!\n")
            print("Temporary AWS Credentials Granted via OneLogin\n")
            print("Updated AWS profile '%s' located at %s" % (options.profile_name, options.file))
            if loops > (i + 1):
                print("This process will regenerate credentials %s more times.\n" % (loops - (i + 1)))
                print("Press Ctrl + C to exit")



if __name__ == '__main__':
    main()
