"""BE-03 HTTP reference validation and S3 delivery; no FITS processing or foreign DB reads."""
import json
import os
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import boto3
from botocore.config import Config


def resolve_product(product_id):
    base = os.getenv('OBSERVATION_SERVICE_URL', 'http://observation:8000').rstrip('/')
    try:
        with urlopen(Request(f'{base}/api/v1/observations/temporal/{product_id}/'), timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def frame_key(result, job_id, index):
    if not isinstance(result, dict) or result.get('job_id') != str(job_id) or result.get('status') != 'completed':
        raise ValueError('Invalid completed timelapse result')
    frames = result.get('frames', [])
    if not 0 <= index < len(frames) or frames[index].get('index') != index:
        raise IndexError('Frame not found')
    key = frames[index].get('object_key', '')
    pattern = rf'timelapses/{re.escape(str(job_id))}/[0-9a-f]{{24}}/frame-{index:03d}\.png'
    if not re.fullmatch(pattern, key):
        raise ValueError('Frame outside authorized job prefix')
    return key


def open_png(key):
    client = boto3.client('s3', endpoint_url=os.getenv('S3_ENDPOINT_URL'),
                          aws_access_key_id=os.getenv('S3_ACCESS_KEY'),
                          aws_secret_access_key=os.getenv('S3_SECRET_KEY'),
                          config=Config(connect_timeout=3, read_timeout=15,
                                        retries={'total_max_attempts': 2}, s3={'addressing_style': 'path'}))
    response = client.get_object(Bucket=os.environ['S3_BUCKET'], Key=key)
    if response.get('ContentType') != 'image/png':
        response['Body'].close()
        raise ValueError('Stored frame is not PNG')
    return response
