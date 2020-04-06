#!/bin/sh
set -e
# set -o xtrace

### VARIABLES
[ -z "$HOME" ] && HOME="~"
TODAY=$(date '+%Y-%m-%d')
AWS_DIR="$HOME/.aws"
BACKUP_DIR="$AWS_DIR-$TODAY"
ONE_LOGIN_INSTALLER_DIR="/tmp/onelogin-installer-$TODAY"

# If you are not setting these environment variables, make sure to pass them via command line parameters
# ONELOGIN_CLIENT_ID
# ONELOGIN_CLIENT_SECRET
# ONELOGIN_APP_ID
ONELOGIN_DURATION=${ONELOGIN_DURATION:-43200}

### FUNCTIONS

function usage() {
	echo "Quick installer for OneLogin AWS credentials generator"
        echo "Usage: $0 <email address>"
        echo "USAGE EXAMPLES:"
        echo "  $0 ilya.gerlovin@ironsrc.com"
        exit 1
}

### MAIN

# Parse options

while [[ $# > 0 ]]; do
    case "$1" in
        --client_id)
            ONELOGIN_CLIENT_ID=$2 && shift && shift
            ;;
        --client_secret)
            ONELOGIN_CLIENT_SECRET=$2 && shift && shift
            ;;
        --app_id)
            ONELOGIN_APP_ID=$2 && shift && shift
            ;;
        --duration)
            ONELOGIN_DURATION=$2 && shift && shift
            ;;
        *)
            break
            ;;
    esac
done

EMAIL=$1

# Parameters validation
[ -z "$EMAIL" ] && echo "Error: must email address" && usage
[ -z "$ONELOGIN_CLIENT_ID" ] && echo "Error: must specify client id with '--client_id' parameter" && usage
[ -z "$ONELOGIN_CLIENT_SECRET" ] && echo "Error: must specify client secret with '--client_secret' parameter" && usage
[ -z "$ONELOGIN_APP_ID" ] && echo "Error: must specify app id with '--app_id' parameter" && usage


if [ ! -d "$BACKUP_DIR" ]; then
  echo mv $AWS_DIR $BACKUP_DIR
  [ -d "$AWS_DIR" ] && mv $AWS_DIR $BACKUP_DIR
fi

[ ! -d $AWS_DIR ] && mkdir $AWS_DIR

cat <<CREDENTIALS_FILE>> $AWS_DIR/credentials

CREDENTIALS_FILE
cat <<ONELOGIN_SDK> $AWS_DIR/onelogin.sdk.json
{
  "client_id": "${ONELOGIN_CLIENT_ID}",
  "client_secret": "${ONELOGIN_CLIENT_SECRET}",
  "region": "us",
  "ip": ""
}
ONELOGIN_SDK

cat <<ONELOGIN_AWS> $AWS_DIR/onelogin.aws.json
{
  "duration": ${ONELOGIN_DURATION},
  "app_id": "${ONELOGIN_APP_ID}",
  "subdomain": "ironsrc",
  "username": "$EMAIL",
  "profile": "default",
  "aws_region": "us-east-1"
}
ONELOGIN_AWS

[ -d "$ONE_LOGIN_INSTALLER_DIR" ] && echo "Fatal error: '$ONE_LOGIN_INSTALLER_DIR' directory already exists. Delete it and try again." && exit 1
mkdir -p $ONE_LOGIN_INSTALLER_DIR && pushd $ONE_LOGIN_INSTALLER_DIR
# git clone git@github.com:SupersonicAds/onelogin-python-aws-assume-role.git
git clone 'https://github.com/SupersonicAds/onelogin-python-aws-assume-role.git'
cd onelogin-python-aws-assume-role
git checkout IRONSRC-RELEASE
echo "Running sudo python setup.py install... please be ready to enter sudo password"
sudo python setup.py install
popd
sudo rm -rf $ONE_LOGIN_INSTALLER_DIR/onelogin-python-aws-assume-role && rmdir $ONE_LOGIN_INSTALLER_DIR
