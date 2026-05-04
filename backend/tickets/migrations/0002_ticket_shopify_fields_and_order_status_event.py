# Hand-written to match Django 4.2.9 autogenerator output.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='shopify_order_id',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name='ticket',
            name='resolved_by',
            field=models.CharField(blank=True, choices=[('agent', 'Agent'), ('human', 'Human')], max_length=10),
        ),
        migrations.CreateModel(
            name='OrderStatusEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('shopify_order_id', models.CharField(db_index=True, max_length=64)),
                ('topic', models.CharField(choices=[('orders/updated', 'Order Updated'), ('orders/fulfilled', 'Order Fulfilled')], max_length=32)),
                ('financial_status', models.CharField(blank=True, max_length=32)),
                ('fulfillment_status', models.CharField(blank=True, max_length=32)),
                ('received_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-received_at'],
            },
        ),
        migrations.AddIndex(
            model_name='orderstatusevent',
            index=models.Index(fields=['shopify_order_id', '-received_at'], name='tickets_ord_shopify_8f3c9d_idx'),
        ),
    ]
