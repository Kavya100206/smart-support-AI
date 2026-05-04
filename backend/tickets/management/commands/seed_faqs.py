"""Management command: seed_faqs

Inserts 12 e-commerce support FAQ entries into the FAQEntry table and
computes their sentence-transformer embeddings.

IDEPOTENT — uses get_or_create on the question field so re-running never
creates duplicates. If an entry already has an embedding it is skipped;
pass --reembed to force re-computation.

WHERE TO RUN
------------
This command must be run on your LOCAL machine (not inside Docker).
sentence-transformers / PyTorch is intentionally NOT installed in the
Docker image to keep build times fast.

Local setup (one-time):
    pip install sentence-transformers   # installs PyTorch automatically

Then, pointing at the live DB (via DATABASE_URL env var or .env):
    python manage.py seed_faqs

The computed 384-dim embeddings are stored as JSON in FAQEntry.embedding.
The Docker container loads them from the DB at startup via faq_service.py
without ever needing sentence-transformers.

Usage:
    python manage.py seed_faqs
    python manage.py seed_faqs --reembed   # force re-embed all entries
"""
import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

# 12 e-commerce support FAQ Q&A pairs covering the main ticket categories.
FAQ_DATA = [
    {
        "question": "Where is my order?",
        "answer": (
            "You can track your order by visiting the tracking link sent to "
            "your email after shipment. Orders typically ship within 1-2 "
            "business days and arrive within 5-7 business days."
        ),
    },
    {
        "question": "How do I request a refund?",
        "answer": (
            "Refunds are available within 30 days of purchase for paid orders. "
            "Contact our support team with your order ID and reason for the "
            "refund. Refunds are processed within 5-7 business days back to "
            "your original payment method."
        ),
    },
    {
        "question": "Can I cancel my order?",
        "answer": (
            "Orders can be cancelled within 1 hour of placement if they have "
            "not yet been fulfilled. After fulfilment has begun, cancellations "
            "are no longer possible. Please contact support immediately with "
            "your order ID."
        ),
    },
    {
        "question": "My payment was declined. What should I do?",
        "answer": (
            "Please verify your card details, billing address, and available "
            "balance. If the issue persists, try a different payment method or "
            "contact your bank. Our team can also check for any payment "
            "processing errors on our end."
        ),
    },
    {
        "question": "How do I change my shipping address?",
        "answer": (
            "Shipping addresses can be updated before the order is fulfilled. "
            "Contact support with your order ID and the new address as soon as "
            "possible. Once an order has shipped, address changes are not "
            "possible."
        ),
    },
    {
        "question": "I received the wrong item. What do I do?",
        "answer": (
            "We apologise for the error. Please contact support with your order "
            "ID and a photo of the item received. We will arrange a replacement "
            "or full refund at no extra cost to you."
        ),
    },
    {
        "question": "How do I reset my password?",
        "answer": (
            "Click 'Forgot Password' on the login page and enter your email "
            "address. You will receive a password-reset link within a few "
            "minutes. Check your spam folder if it does not arrive."
        ),
    },
    {
        "question": "How do I update my billing information?",
        "answer": (
            "Log in to your account, navigate to Account Settings → Payment "
            "Methods, and update your card details there. Changes apply to "
            "future orders immediately."
        ),
    },
    {
        "question": "My item arrived damaged. What should I do?",
        "answer": (
            "We're sorry to hear that. Please contact support within 7 days of "
            "delivery with your order ID and photos of the damage. We will "
            "send a replacement or issue a full refund as quickly as possible."
        ),
    },
    {
        "question": "Do you offer free shipping?",
        "answer": (
            "Free standard shipping is available on all orders over $50. "
            "Orders below that threshold are charged a flat $5.99 shipping fee. "
            "Expedited and overnight options are available at checkout for an "
            "additional cost."
        ),
    },
    {
        "question": "Can I return a sale or discounted item?",
        "answer": (
            "Sale items are eligible for store credit only, not cash refunds, "
            "within 14 days of purchase. Items marked 'Final Sale' cannot be "
            "returned or exchanged."
        ),
    },
    {
        "question": "How do I apply a discount code?",
        "answer": (
            "Enter your discount code in the 'Promo Code' field at checkout "
            "and click Apply. Only one code may be used per order. If your "
            "code does not work, please contact support and we will verify "
            "its validity."
        ),
    },
]


class Command(BaseCommand):
    help = "Seed FAQEntry table with e-commerce FAQ data and compute embeddings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reembed",
            action="store_true",
            help="Force re-computation of embeddings even if they already exist.",
        )

    def handle(self, *args, **options):
        reembed: bool = options["reembed"]

        # sentence-transformers is a LOCAL-ONLY dependency (not in Docker).
        # If this command is accidentally run inside the container it will
        # fail here with a clear message instead of a confusing ImportError.
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        except ImportError as exc:
            raise CommandError(
                "sentence-transformers is not installed in this environment.\n\n"
                "This command must be run on your LOCAL machine, not inside Docker.\n"
                "Install it locally with:  pip install sentence-transformers\n"
                "Then run:                 python manage.py seed_faqs\n\n"
                "The Docker image does not need sentence-transformers — it loads\n"
                "pre-computed embeddings from the database at runtime."
            ) from exc

        from tickets.models import FAQEntry  # noqa: PLC0415

        self.stdout.write("Loading sentence-transformers model ...")
        encoder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        self.stdout.write(self.style.SUCCESS("Model loaded."))

        created_count = 0
        updated_count = 0

        for item in FAQ_DATA:
            entry, created = FAQEntry.objects.get_or_create(
                question=item["question"],
                defaults={"answer": item["answer"]},
            )

            if created:
                created_count += 1
            else:
                if entry.answer != item["answer"]:
                    entry.answer = item["answer"]

            needs_embed = entry.embedding is None or reembed
            if needs_embed:
                vec = encoder.encode(item["question"], convert_to_numpy=True)
                entry.embedding = vec.tolist()
                entry.save(update_fields=["answer", "embedding"])
                updated_count += 1
            elif not created:
                entry.save(update_fields=["answer"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created: {created_count}, embeddings computed: {updated_count}."
            )
        )
