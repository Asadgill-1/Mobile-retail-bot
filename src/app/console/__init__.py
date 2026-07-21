"""Platform-owner console channel (migrations 024/025).

The console is a Vercel web app: it can reach Supabase but NOT this PC's Redis. Instead of
exposing the PC through a tunnel, the two sides talk through two tables:

  platform_settings  — console writes config (AI provider/model/key), backend reads it
  redis_ops          — console writes intents, the 60s health beat executes them here

so nothing on the owner's machine needs a public address.
"""
