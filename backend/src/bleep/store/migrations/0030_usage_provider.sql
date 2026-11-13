-- The provider a model call went through (#547): `anthropic`, `openrouter` or `local`. A model id
-- alone does not say whether Claude ran through Anthropic or through OpenRouter. Rows from before,
-- and calls whose client names no provider, keep NULL, which the usage API reports as unknown.
ALTER TABLE usage ADD COLUMN provider TEXT;
