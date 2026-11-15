"""Direct processing entry point for local verification; job_id belongs to BE-03."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from services.image_processing.infrastructure.tasks import render_timelapse


class Command(BaseCommand):
    help = "Process 1-10 previously discovered Observation products; upload outputs to S3."

    def add_arguments(self, parser):
        parser.add_argument("--request-file", type=Path, required=True)
        parser.add_argument("--manifest-file", type=Path, required=True)

    def handle(self, *args, **options):
        try:
            payload = json.loads(options["request_file"].read_text(encoding="utf-8"))
            result = render_timelapse.run(**payload)
            options["manifest_file"].write_text(json.dumps(result, indent=2), encoding="utf-8")
        except Exception as exc:
            raise CommandError(f"Timelapse failed ({type(exc).__name__}); no completed result returned") from exc
        self.stdout.write(f"Persisted {len(result['frames'])} frames and manifest")
