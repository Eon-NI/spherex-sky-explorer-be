from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [migrations.CreateModel(
        name="ObservationRecord",
        fields=[
            ("publisher_did", models.CharField(max_length=512, primary_key=True, serialize=False)),
            ("collection", models.CharField(max_length=100)),
            ("access_url", models.URLField(max_length=2048)),
            ("ra_deg", models.FloatField(null=True)),
            ("dec_deg", models.FloatField(null=True)),
            ("last_seen_at", models.DateTimeField(auto_now=True)),
        ],
        options={"db_table": "observations"},
    )]
