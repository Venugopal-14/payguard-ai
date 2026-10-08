# Security

PayGuard AI is a security-focused payment orchestration project. Its entire
premise is that an AI agent must **never** have unrestricted control of money.

## The core guarantee

```
AI agent  ->  proposes a purchase (never pays)
                     |
             Risk engine (deterministic score)
                     |
             Policy engine (deterministic decision)
                     |
   BLOCK  /  NEEDS_APPROVAL (human)  /  APPROVE
                     |
             PayPal Sandbox (only this layer holds credentials)
```

The agent returns a *proposal*. Deterministic, auditable engines decide whether
that proposal may become a real PayPal order. A `BLOCK` decision can never
create an order. A `NEEDS_APPROVAL` decision requires an explicit human approve
call before execution.

## Secrets

Never commit:

- PayPal client ID / secret
- AI provider API keys
- database passwords

All secrets are provided through environment variables (`.env`), which is
git-ignored. Only `.env.example` (with blank values) is committed.

## Environment

Development and testing use the **PayPal Sandbox** only. No production payment
credentials are used anywhere in this project.
