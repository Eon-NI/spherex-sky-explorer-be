"""S3/MinIO storage; FITS bytes move through files, never task payloads."""
import os
from pathlib import Path
import boto3
from botocore.exceptions import ClientError
from services.image_processing.domain.models import ImageReference


class S3ScientificObjectStore:
    def __init__(self) -> None:
        self.bucket = os.environ["S3_BUCKET"]
        self.client = boto3.client(
            "s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
            aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
        )

    def exists(self, reference: ImageReference) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=reference.object_key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def upload_file(self, path: Path, reference: ImageReference) -> None:
        self.client.upload_file(str(path), self.bucket, reference.object_key,
                                ExtraArgs={"ContentType": reference.content_type})

    def download_file(self, reference: ImageReference, path: Path) -> None:
        self.client.download_file(self.bucket, reference.object_key, str(path))

    def open_stream(self, reference: ImageReference):
        response = self.client.get_object(Bucket=self.bucket, Key=reference.object_key)
        return response["Body"], response.get("ContentLength")
