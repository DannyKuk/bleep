# Incident

{subject}

## What opened it

{signals}{look}

## Containers

{containers}

## Recent logs

{logs}

## Host

{host}

## Rules

Host rules:
{host_rules}

Service rules:
{service_rules}

## Bleep's memory

These are Bleep's own records of earlier incidents on these hosts, not data from a host. {untrusted_rule} A past cause is a lead to check, not proof of this one, and a human's past decision says what they wanted then.

Similar past incidents, the same service's first:
{similar}

Facts about this service:
{facts}

## Could not be read

{gaps}

## Your task

Find out what went wrong and why. Your tools are read-only: the container list, inspect, logs, stats, Docker events, a few host commands and, when they are offered, a search of past incidents, the service repo's recent commits and their diffs, and a knowledge search of the Docker docs, the services' repo docs and past reports. Use them for what the context above does not settle yet: the containers involved, the ones they depend on, and the order things happened in. You have at most {budget} tool calls, so spend them on what matters most.

When the cause is a setting (an environment variable, a config value) and fixing it needs a value the context above does not give (the value it should have, not the one it has), search the service's docs for that setting by its exact name before you stop: `knowledge_search` with `sources` set to `repo`, when it is offered. The fix is planned from what you find.

When you know enough, or the tools have nothing more to show, stop calling tools and answer in a few sentences: what happened, the most likely cause, and the evidence for it (which tool showed what). For such a setting, say what its docs give as its value and on which page, or that they give none. Say plainly what you could not find out.
