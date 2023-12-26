# This is a simple test server for use in troubleshooting workshops.
# It is based on the AWS Well-Architected labs and simulates a web
# application for recommending TV shows to users.
#
# This code is only for use in practice labs to demonstrate
# troubleshooting methodologies.
#
# *** NOT FOR PRODUCTION USE ***
#
# Licensed under the Apache 2.0 and MITnoAttr License.
#
# Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at https://aws.amazon.com/apache2.0/

__author__    = "Seth Eliot, David Schliemann"
__email__     = "seliot@amazon.com, schliema@amazon.com"
__copyright__ = "Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved."
__credits__   = ["Seth Eliot", "Adrian Hornsby", "David Schliemann"]

import sys
import getopt
import boto3
import json
import random
import logging
import requests
import dns.resolver
from datetime import datetime
from boto3.s3.transfer import TransferConfig
from http.server import BaseHTTPRequestHandler, HTTPServer
from functools import partial
from ec2_metadata import ec2_metadata
from os import curdir, sep
from aws_xray_sdk.core import xray_recorder, patch_all
patch_all()

# Configure X-ray, logging and S3 transfer config.
xray_recorder.configure(service='AWS Support 302 Workshop Networking App', context_missing='IGNORE_ERROR')
logging.getLogger('aws_xray_sdk').setLevel(logging.ERROR)
xfer_config = TransferConfig(use_threads=False)
logging.basicConfig(filename='server.log',
                    format='%(asctime)s:SupportTroubleshootingNetworkApp:%(levelname)s - %(message)s',
                    level=logging.WARNING)

# HTML code template for the health check page.
hc_html = """
<!DOCTYPE html>
<html>
    <head>
        <meta charset="utf-8">
        <title>{Title}</title>
        <link rel="icon" type="image/ico" href="https://a0.awsstatic.com/main/images/site/fav/favicon.ico" />
    </head>
    <body>
        <p>{Content}</p>
    </body>
</html>"""

def put_parameter_store(name, value, region):
    try:
        parameter_client = boto3.client('ssm', region_name=region)
        response = parameter_client.put_parameter(
            Name=name,
            Value=value,
            Type='String',
            Overwrite=True
        )
    except Exception as e:
        logging.warning(e)


# RequestHandler: Handle incoming HTTP Requests.
# Response depends on type of request made.
class RequestHandler(BaseHTTPRequestHandler):
    def __init__(self, region, bucket, *args, **kwargs):
        self.region = region
        self.bucket = bucket
        super().__init__(*args, **kwargs)

    def do_GET(self):
        # Default request URL without additional path info ("main page")
        if self.path == '/':
            # Start tracing with X-Ray.
            segment = xray_recorder.begin_segment('/')

            # Call dependencies, including SSM for parameters,
            # DynamoDB to fetch user name and favourite movie,
            # S3 for assets, EC2 Meta-data for configuration,
            # external service for dependencies and VPC DNS.
            # Each function returns the result of the test and
            # the time taken.
            ssmtest, ssm_time = call_SSM(self.region)
            ddbtest, ddb_time = call_dynamoDB(self.region)
            s3test, s3_time = call_S3(self.region, self.bucket)
            mdtest, md_time, metadata = get_metadata(False, self.region)
            extservertest, ext_time = call_extServer(self.region)
            dnstest, dns_time = call_DNS(self.region)

            # Transform retults into colour-coded HTML
            ssmoutput = '<span class="w3-text-green">SUCCESS</span>' if ssmtest == 'SUCCESS' else '<span class="w3-text-red">FAILED</span>'
            ddboutput = '<span class="w3-text-green">SUCCESS</span>' if ddbtest == 'SUCCESS' else '<span class="w3-text-red">FAILED</span>'
            s3output = '<span class="w3-text-green">SUCCESS</span>' if s3test == 'SUCCESS' else '<span class="w3-text-red">FAILED</span>'
            mdoutput = '<span class="w3-text-green">SUCCESS</span>' if mdtest == 'SUCCESS' else '<span class="w3-text-red">FAILED</span>'
            extoutput = '<span class="w3-text-green">SUCCESS</span>' if extservertest == 'SUCCESS' else '<span class="w3-text-red">FAILED</span>'
            dnsoutput = '<span class="w3-text-green">SUCCESS</span>' if dnstest == 'SUCCESS' else '<span class="w3-text-red">FAILED</span>'

            # Send successful response status code.
            self.send_response(200)

            # Send headers.
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            # Get HTML template.
            f = open(curdir + sep + "content.html", 'r')
            html = f.read()

            # Fill in template and write html output.
            self.wfile.write(
                bytes(
                    html.format(SSMTestString=ssmoutput, SSMTime=ssm_time, DDBTestString=ddboutput,
                                DDBTime=ddb_time, S3TestString=s3output, S3Time=s3_time, MetadataTestString=mdoutput,
                                MetaDataTime=md_time, ExtServerTestString=extoutput, ExtGetTime=ext_time,
                                DNSTestString=dnsoutput, DNSGetTime=dns_time,BucketNameString=self.bucket,RegionNameString=self.region),
                    "utf-8"
                )
            )

            # Stop recording.
            xray_recorder.end_segment()

            return

        # Healthcheck request - this will be used by the Elastic Load Balancer.
        # Note we send a custom response code (HTTP 299) to indicate success.

        elif self.path == '/healthcheck':
            #subsegment = xray_recorder.begin_subsegment('/healthcheck')
            # Return a success status code
            self.send_response(299)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            message = "<h1>Success, server is HEALTHY</h1>"

            # Add metadata
            mdtime, mdtest, metadata  = get_metadata(True, self.region)
            message += metadata

            self.wfile.write(
                bytes(
                    hc_html.format(Title="healthcheck", Content=message),
                    "utf-8"
                )
            )
        # elif self.path == '/taskcheck':
        #     self.send_response(200)
        #     self.send_header('Content-type', 'application/json')
        #     self.end_headers()
        #     message = {}
        #     message['ssmtest'], ssm_time = call_SSM(self.region)
        #     message['ddbtest'], ddb_time = call_dynamoDB(self.region)
        #     message['s3test'], s3_time = call_S3(self.region, self.bucket)
        #     message['mdtest'], md_time, metadata = get_metadata(False)
        #     message['extservertest'], ext_time = call_extServer()
        #     message['dnstest'], dns_time = call_DNS()
        #     json_data = json.dumps(message)
        #     self.wfile.write(json_data.encode('utf-8'))

        else:
            # Return 404, page not found.
            self.send_response(404)
            self.end_headers()

        return

# Call S3 to get web assets.
# Parameters:
# region - which region (e.g. us-east-1) to call.
# bucket - the bucket the assets are stored in.
# Returns:
# result - result of function, SUCCESS or FAILED
# time_taken - time taken for this function to execute.
def call_S3(region, bucket):
    start_time = datetime.now()
    session = boto3.Session()

    # Get image from S3. Located at s3://BucketName/artifacts/three-tier-webstack/s3_get_green_checkmark.png
    try:
        # Setup client for S3 -- we use this for parameters used as a
        # enable/disable switch in the lab
        s3 = session.client('s3', region_name=region)
        s3.download_file(bucket, 'artifacts/three-tier-webstack/s3_get_green_checkmark.png', 's3_get_green_checkmark.png', Config=xfer_config)
        result = "SUCCESS"
        put_parameter_store('call_S3', result, region)
    except Exception as e:
        logging.warning('Call to S3 VPC Endpoint failed.')
        logging.warning(e)
        result = "FAILED"
        put_parameter_store('call_S3', result, region)

    end_time = datetime.now()
    s3time = (end_time - start_time)

    return result, round(s3time.total_seconds() * 1000,2)

# Call VPC DNS to ensure resolution working.
# Parameters:
# None
# Returns:
# result - result of function, SUCCESS or FAILED
# time_taken - time taken for this function to execute.
def call_DNS(region):
    # X-ray is already recording for the top-level "GET /" Segment.
    # As this is custom code, we manually instrument it.
    subsegment = xray_recorder.begin_subsegment('VPC DNS Test')
    start_time = datetime.now()
    try:
        dnsresult = dns.resolver.resolve('aws.amazon.com', 'A')
        result = "SUCCESS"
        put_parameter_store('call_DNS', result, region)
    except Exception as e:
        logging.warning('Call to VPC DNS failed.')
        logging.warning(e)
        result = "FAILED"
        put_parameter_store('call_DNS', result, region)
    end_time = datetime.now()
    dnstime = (end_time - start_time)
    xray_recorder.end_subsegment()

    return result, round(dnstime.total_seconds() * 1000,2)

# Check connectivity to external dependency (1.1.1.1)
# Parameters:
# None
# Returns:
# result - result of function, SUCCESS or FAILED
# time_taken - time taken for this function to execute.
def call_extServer(region):
    # X-ray is already recording for the top-level "GET /" Segment.
    # We manually instrument this function for readability.
    subsegment = xray_recorder.begin_subsegment('External Dependency')
    start_time = datetime.now()
    try:
        requests.get("https://1.1.1.1", timeout=0.2)
        result = "SUCCESS"
        put_parameter_store('call_extServer', result, region)
    except Exception as e:
        logging.warning('Call to 1.1.1.1 failed.')
        logging.warning(e)
        result = "FAILED"
        put_parameter_store('call_extServer', result, region)

    end_time = datetime.now()
    ext_server_time = (end_time - start_time)
    xray_recorder.end_subsegment()

    return result, round(ext_server_time.total_seconds() * 1000,2)

# Retrieve EC2 Metadata to show students
# which instance / AWS AZ they are hitting.
# Parameters:
# trace_func - Whether or not to trace this function. values: True/False
# Returns:
# result - result of function, SUCCESS or FAILED
# time_taken - time taken for this function to execute.
# metadata - string containing the EC2 meta-data retrieved.
def get_metadata(healthcheck, region):
    # Skip X-ray for health checks.
    if not healthcheck:
        subsegment = xray_recorder.begin_subsegment('Metadata')

    start_time = datetime.now()
    metadata = '<b>Metadata:</b><br>'
    try:
        message_parts = [
            'availability_zone: %s' % ec2_metadata.availability_zone,
            'instance_id: %s' % ec2_metadata.instance_id,
            'instance_type: %s' % ec2_metadata.instance_type,
            'private_hostname: %s' % ec2_metadata.private_hostname,
            'private_ipv4: %s' % ec2_metadata.private_ipv4
        ]
        metadata += '<br>'.join(message_parts)
        result = "SUCCESS"
        put_parameter_store('get_metadata', result, region)
    except Exception as e:
        metadata += "ERROR. Failure getting metadata - is this running outside AWS?"
        logging.warning('Call to EC2 Meta-data failed.')
        logging.warning(e)
        result = "FAILED"
        put_parameter_store('get_metadata', result, region)

    end_time = datetime.now()
    mdtime =  (end_time - start_time)
    if not healthcheck:
        xray_recorder.end_subsegment()
    return result, round(mdtime.total_seconds() * 1000,2), metadata

# Call AWS Systems Manager (SSM) to get app parameters.
# Parameters:
# region - which region (e.g. us-east-1) to call.
# Returns:
# result - result of function, SUCCESS or FAILED
# time_taken - time taken for this function to execute.
def call_SSM(region):
    session = boto3.Session()
    start_time = datetime.now()
    try:
        # Setup client for SSM -- we use this for parameters used as a
        # enable/disable switch in the lab
        ssm_client = session.client('ssm', region_name=region)
        value = ssm_client.get_parameter(Name='RecommendationServiceEnabled')
        result = "SUCCESS"
        put_parameter_store('call_SSM', result, region)
    except Exception as e:
        logging.warning('Call to SSM failed.')
        logging.warning(e)
        result = "FAILED"
        put_parameter_store('call_SSM', result, region)

    end_time = datetime.now()
    ssmtime =  (end_time - start_time)

    return result, round(ssmtime.total_seconds() * 1000,2)

# This method mocks the call to the RecommendationService.
# Calls to the getRecommendation API are actually get_item
# calls to a dynamoDB table.
# Parameters:
# region - which region (e.g. us-east-1) to call.
# Returns:
# result - result of function, SUCCESS or FAILED
# time_taken - time taken for this function to execute.
def call_dynamoDB(region):
    start_time = datetime.now()

    # Generate User ID between 1 and 4
    # This currently uses a randomly generated user.
    # In the future maybe allow student to supply the user ID as input
    user_id = str(random.randint(1, 4))


    table_name = "RecommendationService"
    # Call the RecommendationService
    # (actually just a simple lookup in a DynamoDB table,
    # which is acting as a mock for the RecommendationService).
    try:
        # Setup client for DDB -- we will use this to mock a service dependency
        # It would be more efficient to create the clients once on init.
        # But in the lab we change permissions on the EC2 instance,
        # and this way we are sure to pick up the new credentials.
        session = boto3.Session()
        ddb_client = session.client('dynamodb', region)

        response = ddb_client.get_item(
            TableName=table_name,
            Key={
                'ServiceAPI': {
                    'S': 'getRecommendation',
                },
                'UserID': {
                    'N': user_id,
                }
            }
        )
        result = "SUCCESS"
        put_parameter_store('call_dynamoDB', result, region)
    except Exception as e:
        logging.warning('Call to DynamoDB VPC Endpoint failed.')
        logging.warning(e)
        result = "FAILED"
        put_parameter_store('call_dynamoDB', result, region)

    end_time = datetime.now()
    ddbtime =  (end_time - start_time)

    return result, round(ddbtime.total_seconds() * 1000,2)

# Initialize server
def run(argv):
    try:
        opts, args = getopt.getopt(
            argv,
            "hs:p:r:b:",
            [
                "help",
                "server_ip=",
                "server_port=",
                "region=",
                "bucket="
            ]
        )
    except getopt.GetoptError:
        print('server.py -s <server_ip> -p <server_port> -r <AWS region> -b <S3 bucket>')
        logging.error(e)
        sys.exit(2)
    print(opts)

    # Default value - will be over-written if supplied via args
    server_port = 80
    server_ip = '0.0.0.0'
    try:
        region = ec2_metadata.region
    except:
        region = 'us-east-2'

    # Get commandline arguments
    for opt, arg in opts:
        if opt in ("-h", "--help"):
            print('server.py -s <server_ip> -p <server_port> -r <AWS region> -b <S3 bucket>')
            sys.exit()
        elif opt in ("-s", "--server_ip"):
            server_ip = arg
        elif opt in ("-p", "--server_port"):
            server_port = int(arg)
        elif opt in ("-r", "--region"):
            region = arg
        elif opt in ("-b", "--bucket"):
            bucket = arg

    # start server
    print('starting server...')
    server_address = (server_ip, server_port)

    handler = partial(RequestHandler, region, bucket)
    httpd = HTTPServer(server_address, handler)
    print('running server...')
    httpd.serve_forever()


if __name__ == "__main__":
    run(sys.argv[1:])
