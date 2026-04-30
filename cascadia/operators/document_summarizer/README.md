# Document Summarizer (C6)

**ID:** document-summarizer | **Port:** 8106 | **Tier:** lite

Extractive document summarization — no LLM required. Scores sentences by normalized word frequency (TF) and returns the top N in original order.

## Actions

| Action | Approval Required | Description |
|---|---|---|
| `summarize_text` | No | Summarize a text string |
| `summarize_file` | No | Read and summarize a local file (txt/md/csv) |
| `extract_keywords` | No | Return top-N keywords by frequency |
| `export_summary` | **Yes** | Write summary to a local file |

## NATS Subjects

- Call: `cascadia.operators.document-summarizer.call`
- Response: `cascadia.operators.document-summarizer.response`
- Approvals: `cascadia.approvals.request`

## Example Payload

```json
{
  "action": "summarize_text",
  "params": {
    "text": "Your long document text here...",
    "max_sentences": 5
  }
}
```

## Health

```
GET http://localhost:8106/health
→ {"status":"ok","operator":"document-summarizer","version":"1.0.0","port":8106}
```

## Running

```bash
bash install.sh
python3 operator.py
```
