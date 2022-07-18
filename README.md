## Table of contents
* [General info](#general-info)
* [Components](#components)
* [Setup](#setup)

## General info
App: Panda - Watchguard endpoint protection
Panda is enpoint protection technology, the api is defined in:
https://www.watchguard.com/help/docs/API/Content/en-US/panda/aether_endpoint_security/v1/aether_endpoint_security.html
This app extracts the portal data on a regular basis, and ingests the data into sumo CIP.
It also provides basic dashboarding.

## Components
The Sumo Logic Panda app consists of the following components:

* panda.py
AWS Lambda function: runs every few hours in order to check on new (not earlier collected)  logs. If these are available, they are downloaded, and forwarded as JSON onto the designated sumo logic endpoint.
This function can be ran as a lambda function in an AWS account, or alternately on any system, driven by cron.
In the latter case the command has to be ran with following options:
```
./panda.py -n  -a <authentication url> -i <accessid> -p <passwd> -k <apikey> --endpoint https:<sumologic collector endpoint>
```
Example of crontab entry in /etc/crontab to regulary run the command:
```
36 */2	* * *	ubuntu	/home/ubuntu/panda.py --n  -a <authentication url> -i <accessid> -p <passwd> -k <apikey> --endpoint https:<sumologic collector endpoint
```
Pre-requisite is availability of python3 on host system.

* sumopanda.json
AWS CloudFormation template: installs the lambda function, plus additional resources required for the lambda function to properly execute (role, policies, event trigger, parameters).
Sumo Collector for panda logs: to be created in Sumo account by customer.

* Panda.json
json file which contains all dashboards to visualize Panda data. Needs to be imported into a folder in  your Sumo Logic account.

* pandalambda.zip
zipped lambda function, for convenience, to be placed on S3 somehwere acessible by cloudformation script

## Setup
Installation instructions:

In your sumo account:  create hosted HTTP connector, with one HTTP source with unique URL.
Provide following parameters:
* Name: panda
* Check ‘Enable Timestamp Parsing’
* Timestamp format: yyyy-MM-dd'T'hh:mm:ss.SSSSSSZZZZ
* Timestamp Locator: .*"TIMESTAMP"\s*:\s+"([^"]+)"
* Sourcecategory: panda

Make sure that the defined timezone is equal to the timezone where your script runs.

In your AWS account (if collection function will run as AWS lambda code) : 
* Place lambda zip archive (my-deployment-package.zip) on one of your accessible S3 buckets, and remember the access URL to this file.
* Run the provided Cloud Formation template in your AWS environment, and provide it with the proper parameters (for example: panda portal URL, sumo endpoint URL, URL to lambda zip archive, earliest data for logs).

* Once the stack has been created, install the Panda App in your Sumo Logic account., with SourceCategory = panda.

Depending on the cron schedule (by default once an hour) connected with the Lambda function, data will start flowing in, and the dashboards will start populating with data. Please note it may take a bit of time for dashboards to populate (up to few hours).
