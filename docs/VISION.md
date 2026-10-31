# storekeeper — Vision

## Why this exists

Handing an LLM write access to a store is scary: one hallucinated tool call is a
real refund. storekeeper is an open-source AI customer-support agent for Shopify
that takes that problem seriously — it classifies a ticket, checks store policy,
executes order actions behind human approval, and drafts the reply, with the
dangerous steps guarded by code instead of prompt wording.

## The thesis

**An agent is trustworthy when its dangerous paths are enforced by structure,
not prompts.** Every feature must serve one of these three; question anything
that doesn't:

1. **Structure over prompts.** The policy check is pure code on a mandatory
   graph edge — no prompt wording decides a refund, and no ticket text can talk
   the gate out of its rules. The LLM classifies and drafts; it never picks
   actions.
2. **Real actions, real stakes.** Live Shopify store via the GraphQL Admin
   API — not mocks. Every write pauses on a human approval interrupt that
   survives a process restart.
3. **Show the work.** Every run traced in LangSmith; every decision — gate
   verdict, human approval, executed action — auditable end to end.

## Who it's for

Developers who want a self-hostable support agent for a Shopify store they own,
and developers studying how to build guardrailed agents — deterministic gates,
LangGraph interrupts, durable state — from a working reference instead of a
tutorial.



## What this is not

No helpdesk integrations yet (Zendesk / Front / Gorgias). Every reply is a
draft — nothing auto-sends. No web UI in v1. Works on stores you own (a
constraint of Shopify's client-credentials auth). Anything that doesn't sharpen
the thesis waits its turn.
