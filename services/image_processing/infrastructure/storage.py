"""S3/MinIO object-store adapter, ready for future FITS processing."""
import os
import uuid
import boto3
from services.image_processing.domain.models import ImageReference


class S3ScientificObjectStore:
    def __init__(self) -> None:
        self.bucket = os.environ["S3_BUCKET"]
        self.client = boto3.client(
            "s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
            aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
        )

    def read(self, reference: ImageReference) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=reference.object_key)["Body"].read()

    def write(self, data: bytes, content_type: str) -> ImageReference:
        key = f"science/{uuid.uuid4()}"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return ImageReference(key, content_type)
