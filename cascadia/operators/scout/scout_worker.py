import os
import json
import re
import requests
from datetime import datetime


class ScoutWorker:
    def __init__(self, config: dict):
        self.config = config
        self.bridge_url = config.get("bridge_url", "http://localhost:4011")
        self.model = config.get("model", "qwen2.5:3b")
        self.groq_key = config.get("groq_api_key", "")
        self.groq_model = config.get("groq_model", "llama-3.1-8b-instant")
        self.groq_fallback = config.get("groq_fallback", False)
        self.scout_folder = config.get("scout_folder", "scouts/lead-engine")
        self._system_prompt = None

    def _load_folder(self, subfolder: str) -> str:
        path = os.path.join(self.scout_folder, subfolder)
        content = ""
        if not os.path.exists(path):
            return ""
        for fname in sorted(os.listdir(path)):
            if fname.endswith((".md", ".txt")):
                with open(os.path.join(path, fname)) as f:
                    content += f"\n\n{f.read()}"
        return content.strip()

    def _build_system_prompt(self) -> str:
        if self._system_prompt:
            return self._system_prompt
        job = self._load_folder("job_description")
        policy = self._load_folder("company_policy")
        task = self._load_folder("current_task")
        hour = datetime.now().hour
        after_hours = hour < 8 or hour >= 18
        time_note = (
            "\n\nCURRENT STATUS: After business hours. Set expectation that team responds first thing tomorrow morning."
            if after_hours else
            "\n\nCURRENT STATUS: Business hours. Hot leads can expect a call within the hour."
        )
        self._system_prompt = f"{job}\n\n{policy}\n\n{task}{time_note}"
        return self._system_prompt

    def _call_ollama(self, messages: list, timeout: int = 20) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": 0.7,
            "max_tokens": 300
        }
        resp = requests.post(
            f"{self.bridge_url}/v1/chat/completions",
            json=payload,
            timeout=timeout
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    def _call_groq(self, messages: list) -> str:
        if not self.groq_key:
            raise ValueError("No Groq API key configured")
        headers = {
            "Authorization": f"Bearer {self.groq_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.groq_model,
            "messages": messages,
            "max_tokens": 300,
            "temperature": 0.7
        }
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    def _call_ai(self, messages: list) -> str:
        try:
            return self._call_ollama(messages, timeout=12)
        except Exception as e:
            if self.groq_fallback and self.groq_key:
                try:
                    return self._call_groq(messages)
                except Exception:
                    pass
            raise e

    def chat(self, user_message: str, history: list) -> str:
        system = self._build_system_prompt()
        messages = [{"role": "system", "content": system}]
        messages += history[-10:]
        messages.append({"role": "user", "content": user_message})
        return self._call_ai(messages)

    def _regex_fallback(self, conversation_text: str) -> dict:
        """Extract key fields with regex when model JSON fails or is incomplete."""
        data = {}

        # Email
        email_match = re.search(r'[\w.+-]+@[\w-]+\.[a-z]{2,}', conversation_text, re.I)
        if email_match:
            data["email"] = email_match.group()

        # Phone
        phone_match = re.search(r'(\+1[\s-]?)?(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})', conversation_text)
        if phone_match:
            data["phone"] = phone_match.group()

        # Square footage — look for numbers followed by sqft/sq ft/sf keywords
        sqft_match = re.search(
            r'(\d[\d,]*)\s*(?:sq(?:uare)?\.?\s*f(?:ee|oo)?t|sqft|sf)\b',
            conversation_text, re.I
        )
        if sqft_match:
            data["square_footage"] = int(sqft_match.group(1).replace(",", ""))

        # Name from USER lines — look for "my name is X" or "I'm X" or "This is X"
        name_match = re.search(
            r"(?:my name is|i'm|i am|this is|call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
            conversation_text, re.I
        )
        if name_match:
            data["name"] = name_match.group(1).strip()

        # Name from email prefix as fallback
        if not data.get("name") and data.get("email"):
            prefix = data["email"].split("@")[0]
            parts = re.split(r'[._-]', prefix)
            if len(parts) >= 2:
                data["name"] = " ".join(p.capitalize() for p in parts[:2])

        # Location
        loc_match = re.search(
            r'\b(Katy|Houston|Sugar Land|Pearland|Baytown|Conroe|Pasadena|'
            r'Stafford|Humble|Spring|Cypress|League City|Friendswood|'
            r'Galveston|Beaumont|The Woodlands|Clear Lake)\b',
            conversation_text, re.I
        )
        if loc_match:
            data["location"] = loc_match.group(1)

        # Timeline keywords
        if re.search(r'\b(asap|urgent|immediately|right away|this week)\b', conversation_text, re.I):
            data["timeline"] = "immediate"
        elif re.search(r'\bend of (april|may|june|this month)\b', conversation_text, re.I):
            data["timeline"] = "1_month"
        elif re.search(r'\b(2|two)\s*month', conversation_text, re.I):
            data["timeline"] = "2_months"
        elif re.search(r'\b(3|three)\s*month', conversation_text, re.I):
            data["timeline"] = "3_months"
        elif re.search(r'\b(6|six)\s*month', conversation_text, re.I):
            data["timeline"] = "6_months"

        # Project type
        text_low = conversation_text.lower()
        if "retrofit" in text_low or "existing" in text_low:
            data["project_type"] = "warehouse_retrofit"
        elif "new warehouse" in text_low or "new facility" in text_low or "greenfield" in text_low:
            data["project_type"] = "warehouse_new"
        elif "drafting" in text_low or "autocad" in text_low or "drawings" in text_low:
            data["project_type"] = "industrial_drafting"
        elif "rack" in text_low or "racking" in text_low:
            data["project_type"] = "rack_layout"
        elif "dock" in text_low:
            data["project_type"] = "dock_design"

        return data

    def extract_lead(self, history: list) -> dict:
        conversation_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in history
        )

        # Only include USER lines for name/contact extraction hint
        user_lines = "\n".join(
            m['content'] for m in history if m['role'] == 'user'
        )

        extraction_prompt = f"""You are a data extraction assistant. Read this sales conversation and extract the visitor information.

RULES:
- Return ONLY a valid JSON object. No explanation, no markdown, no extra text.
- If a field is not mentioned, use null.
- "name" = the visitor's personal name (e.g. "John Smith"). NOT the company name.
- "square_footage" = the number only as an integer (e.g. 80000). If mentioned as "80,000" return 80000.
- "score" must be exactly one of: hot, warm, cold

VISITOR MESSAGES ONLY (use these to find name and contact info):
{user_lines}

FULL CONVERSATION:
{conversation_text}

Return this exact JSON structure:
{{
  "name": "visitor first and last name or null",
  "email": "email@example.com or null",
  "phone": "phone number or null",
  "company": "company name or null",
  "project_type": "warehouse_new or warehouse_retrofit or industrial_drafting or facility_layout or dock_design or rack_layout or other or unknown",
  "square_footage": 80000,
  "location": "city name or null",
  "timeline": "immediate or 1_month or 2_months or 3_months or 6_months or 12_months or unknown",
  "notes": "2 sentence summary of the project",
  "score": "hot or warm or cold",
  "urgent": true
}}

JSON output:"""

        messages = [{"role": "user", "content": extraction_prompt}]
        data = {}

        # Try AI extraction first
        try:
            raw = self._call_ai(messages)
            raw = raw.strip()
            # Strip markdown fences if model added them
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
            raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
        except Exception:
            pass

        # Always run regex fallback and fill any null/missing fields
        fallback = self._regex_fallback(conversation_text)
        for key, val in fallback.items():
            if not data.get(key) or data[key] in (None, "null", "", 0):
                data[key] = val

        # Sanity check square_footage — if suspiciously small, clear it
        sqft = data.get("square_footage")
        if sqft and (sqft < 100 or sqft > 10_000_000):
            # Try regex only
            sqft_regex = self._regex_fallback(conversation_text).get("square_footage")
            data["square_footage"] = sqft_regex

        # Ensure name is not "null" string
        if data.get("name") in (None, "null", ""):
            data["name"] = None

        # Derive score if missing
        if not data.get("score"):
            has_contact = bool(data.get("email") or data.get("phone"))
            timeline = data.get("timeline", "unknown")
            urgent = data.get("urgent", False)
            if has_contact and (timeline in ("immediate", "1_month") or urgent):
                data["score"] = "hot"
            elif has_contact:
                data["score"] = "warm"
            else:
                data["score"] = "cold"

        data["estimated_value"] = self._estimate_value(
            data.get("project_type"), data.get("square_footage")
        )
        return data

    def _estimate_value(self, project_type: str, sq_footage) -> dict:
        rates = {
            "warehouse_new":       (8,  22),
            "warehouse_retrofit":  (5,  16),
            "industrial_drafting": (6,  18),
            "facility_layout":     (4,  14),
            "dock_design":         (3,  10),
            "rack_layout":         (3,   9),
            "other":               (5,  15),
            "unknown":             (5,  15),
        }
        rate = rates.get(project_type or "unknown", (5, 15))

        if sq_footage and sq_footage > 0:
            lo = int(sq_footage * rate[0])
            hi = int(sq_footage * rate[1])
        else:
            lo, hi = 5000, 25000

        def fmt(v):
            if v >= 1_000_000:
                return f"${v/1_000_000:.1f}M"
            if v >= 1_000:
                return f"${v//1_000}k"
            return f"${v}"

        return {"min": lo, "max": hi, "label": f"{fmt(lo)}–{fmt(hi)}"}
