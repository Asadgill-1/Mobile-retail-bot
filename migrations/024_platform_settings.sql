-- 024: platform_settings — runtime key/value config owned by the PLATFORM owner console
-- (repo owner-dashboard-mobile), read by the backend at runtime.
--
-- Why a table and not just env: the platform owner must be able to switch the AI provider/model
-- (Moonshot | OpenAI | OpenRouter | any OpenAI-compatible base_url) from the web console without
-- shelling into the PC and restarting 7 bots. `llm/llm_client.py::get_llm_client()` overlays these
-- values over env (env stays the fallback) and revalidates on a short interval, so a model switch
-- lands within ~a minute on every process that talks to the LLM.
--
-- SECURITY: `ai_api_key` lives here by explicit owner decision (keys managed in the console).
-- This table is service-role only — RLS is enabled with NO policies, so the anon key can never
-- read it, and the console never ships the key back to the browser (write-only field, masked).
--
-- Also used as the backend→console status channel (no public endpoint, no tunnel — the console
-- reads Supabase directly): the 60s health beat upserts `health_snapshot`, so the console can
-- render live health and treat a stale snapshot as "backend offline".

create table if not exists public.platform_settings (
    key        text primary key,
    value      jsonb not null,
    updated_at timestamptz not null default now()
);

-- Known keys (documented, not constrained — new keys must not need a migration):
--   ai_provider       "moonshot" | "openai" | "openrouter" | "custom"
--   ai_base_url       OpenAI-compatible /v1 base
--   ai_model          chat model id (vision stays env-pinned: settings.ai_vision_model)
--   ai_api_key        provider key (service-role only; console shows ****last4)
--   health_snapshot   {ok, checks{}, metrics{}, quarantined[], at} — written by the health beat

alter table public.platform_settings enable row level security;
-- deliberately NO policies: service-role bypasses RLS, everyone else is denied.
