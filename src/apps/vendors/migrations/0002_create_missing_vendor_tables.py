from django.db import migrations

VENDOR_MODELS = (
    "VendorCategory",
    "Vendor",
    "VendorContact",
    "VendorDocument",
    "VendorRiskRecord",
)


def create_missing_vendor_tables(apps, schema_editor):
    """
    Some databases recorded vendors.0001 without creating vendors_* tables
    because an older vendor_* schema was already present. Create the current
    tables only when they are missing.
    """
    existing = set(schema_editor.connection.introspection.table_names())
    for model_name in VENDOR_MODELS:
        model = apps.get_model("vendors", model_name)
        table_name = model._meta.db_table
        if table_name in existing:
            continue
        schema_editor.create_model(model)
        existing.add(table_name)


class Migration(migrations.Migration):
    dependencies = [
        ("vendors", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_missing_vendor_tables, migrations.RunPython.noop),
    ]
