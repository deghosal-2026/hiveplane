"""Raw Python agent — tool-calling loop with @trace_agent decorator."""
import os, random
os.environ.setdefault('OPENAI_API_KEY', 'omlx-test')
os.environ.setdefault('OPENAI_BASE_URL', 'http://127.0.0.1:8000/v1')

# ── Tools (mock) ────────────────────────────────────────────────────────
def search_kb(query: str) -> dict:
    """Search a mock knowledge base."""
    kb = {
        'password': 'Reset at portal.example.com/settings',
        'billing': 'Contact billing@example.com or call x5001',
        'downtime': 'Scheduled maintenance: Sunday 2-4am UTC',
    }
    for k, v in kb.items():
        if k in query.lower():
            return {'status': 'ok', 'answer': v, 'confidence': random.randint(80, 99)}
    return {'status': 'not_found', 'answer': None, 'confidence': 0}

def lookup_account(account_id: str) -> dict:
    """Look up a mock account."""
    if account_id.startswith('ACC-'):
        return {'status': 'ok', 'account_id': account_id, 'tier': 'pro' if int(account_id[4:]) < 500 else 'basic'}
    return {'status': 'error', 'reason': 'invalid account format'}

def escalate(reason: str) -> dict:
    """Escalate to human operator."""
    return {'status': 'escalated', 'ticket_id': f'TKT-{random.randint(1000,9999)}', 'reason': reason}

# ── Agent logic ─────────────────────────────────────────────────────────
def run_agent(query: str, account_id: str = 'ACC-001') -> dict:
    """Simple agent that searches KB, looks up account, and decides."""
    kb_result = search_kb(query)
    acct_result = lookup_account(account_id)

    if kb_result['status'] == 'ok':
        return {'status': 'success', 'answer': kb_result['answer'], 'account_tier': acct_result.get('tier', 'unknown')}
    else:
        escalate_result = escalate(f"No KB answer for: {query}")
        return {'status': 'escalated', 'ticket': escalate_result['ticket_id'], 'reason': 'no_kb_match'}

if __name__ == '__main__':
    queries = [
        ('reset password', 'ACC-001'),
        ('billing question', 'ACC-999'),
        ('unknown topic', 'ACC-001'),
    ]
    for q, aid in queries:
        result = run_agent(q, aid)
        print(f'Q: {q} → {result["status"]}')