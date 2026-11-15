"""Bounded, idempotent local S3 bucket initialization; no Django/database dependency."""
import os
import sys
from time import sleep
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def wait_ready(endpoint, attempts=12, delay=2):
    opener = build_opener(ProxyHandler({}))
    for attempt in range(attempts):
        try:
            with opener.open(endpoint.rstrip('/') + '/minio/health/ready', timeout=3) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, OSError):
            pass
        if attempt + 1 < attempts:
            sleep(delay)
    raise TimeoutError('MinIO readiness attempt limit exceeded')


def ensure_bucket(client, bucket):
    try:
        client.head_bucket(Bucket=bucket)
        return
    except ClientError as exc:
        if exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode') != 404:
            raise
    try:
        client.create_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') != 'BucketAlreadyOwnedByYou':
            raise
    client.head_bucket(Bucket=bucket)


def main():
    endpoint = os.environ['S3_ENDPOINT_URL']
    bucket = os.environ['S3_BUCKET']
    wait_ready(endpoint)
    client = boto3.client('s3', endpoint_url=endpoint, region_name='us-east-1',
                          aws_access_key_id=os.environ['S3_ACCESS_KEY'],
                          aws_secret_access_key=os.environ['S3_SECRET_KEY'],
                          config=Config(connect_timeout=3, read_timeout=5,
                                        retries={'total_max_attempts': 2},
                                        s3={'addressing_style': 'path'}))
    ensure_bucket(client, bucket)
    print('S3 bucket ready', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - exit safely without credential-bearing adapter details
        print('S3 initialization failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
