import os
import json
import google.generativeai as genai
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

VALID_CATEGORIES = {"billing", "technical", "account", "general"}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}

CLASSIFY_PROMPT = """You are a support ticket classifier. Given a support ticket description, return ONLY a JSON object with two fields:
- "category": one of "billing", "technical", "account", "general"
- "priority": one of "low", "medium", "high", "critical"

Rules:
- billing: payment issues, invoices, charges, refunds, subscriptions
- technical: bugs, errors, crashes, performance, integrations
- account: login, password, profile, permissions, access
- general: anything else

Priority guide:
- critical: system down, data loss, security breach, complete blocker
- high: major feature broken, significant business impact
- medium: partial functionality affected, workaround exists
- low: minor issue, cosmetic, nice-to-have

Return ONLY valid JSON, no explanation, no markdown.

Ticket description: {description}"""


class ClassifyView(APIView):
    def post(self, request):
        description = request.data.get("description", "").strip()
        if not description:
            return Response(
                {"error": "description is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return Response(
                {"suggested_category": None, "suggested_priority": None},
                status=status.HTTP_200_OK,
            )

        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = CLASSIFY_PROMPT.format(description=description)
            result = model.generate_content(prompt)
            raw = result.text.strip()

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            data = json.loads(raw)
            suggested_category = data.get("category", "").lower()
            suggested_priority = data.get("priority", "").lower()

            if suggested_category not in VALID_CATEGORIES:
                suggested_category = None
            if suggested_priority not in VALID_PRIORITIES:
                suggested_priority = None

            return Response({
                "suggested_category": suggested_category,
                "suggested_priority": suggested_priority,
            })

        except Exception:
            return Response(
                {"suggested_category": None, "suggested_priority": None},
                status=status.HTTP_200_OK,
            )
