"use client";

import { Loader2 } from "lucide-react";
import { useId, useState } from "react";

import { SaveError, useSaving } from "@/components/settings/save-error";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { SecretActions, SecretView } from "@/lib/secrets-api";

function Where({ view }: { view: SecretView }) {
  if (!view.set) return <Badge>Missing</Badge>;
  if (view.invalid) return <Badge>Set, but not valid: enter it again</Badge>;
  return <Badge>{view.source === "env" ? "Set in .env" : "Set on the dashboard"}</Badge>;
}

/**
 * One secret, address or id: whether it is set and where from, and an input to set it and a
 * button to clear it unless the environment decides it. The typed value lives only in this input
 * until the save goes through, then the input is emptied; nothing the API sends back holds it.
 * `address` is the address a token is sent to: a token has no input until that is set, and is
 * saved with the version of the address read.
 */
export function SecretField({
  view,
  address,
  actions,
}: {
  view: SecretView;
  address?: SecretView;
  actions: SecretActions;
}) {
  const id = useId();
  const [value, setValue] = useState("");
  const [typedFor, setTypedFor] = useState(address?.version);
  if (typedFor !== address?.version) {
    setTypedFor(address?.version);
    setValue("");
  }
  const { saving, busy, problems, run } = useSaving();
  const isAddress = view.kind === "address";
  const isSecret = view.kind === "secret";
  const owned = view.env_owned;
  const needsAddress = address !== undefined;
  const addressReady = !needsAddress || (address.set && !address.env_owned && !!address.version);

  async function save() {
    if (value === "") return;
    const saved = await run(() => actions.set(view.name, value, address?.version ?? undefined));
    if (saved) setValue("");
  }

  return (
    <div role="group" aria-label={view.name} className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <code className="font-mono text-sm break-all">{view.name}</code>
        <Where view={view} />
        {view.value && (
          <code className="font-mono text-xs break-all text-muted-foreground">{view.value}</code>
        )}
      </div>
      {owned ? (
        <p className="text-xs text-muted-foreground">
          {view.set ? "Set in .env, which wins over the dashboard." : "Decided by .env."} Change it
          there and restart Bleep.
        </p>
      ) : !addressReady ? (
        <p className="text-xs text-muted-foreground">
          {address.env_owned
            ? `${address.name} is set in .env, so ${view.name} is read from .env too.`
            : `Set ${address.name} first: a token is stored for the address it is sent to.`}
        </p>
      ) : (
        <form
          className="flex flex-col gap-1.5"
          onSubmit={(submitted) => {
            submitted.preventDefault();
            void save();
          }}
        >
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor={id} className="sr-only">
              {view.set ? `New value for ${view.name}` : `Value for ${view.name}`}
            </label>
            <Input
              id={id}
              type={isSecret ? "password" : "text"}
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              value={value}
              placeholder={isAddress ? "https://" : view.set ? "Type a new value" : "Type a value"}
              onChange={(changed) => setValue(changed.target.value)}
              className="max-w-sm min-w-48 flex-1 font-mono"
            />
            <Button type="submit" size="sm" disabled={busy || value === ""}>
              {saving && <Loader2 aria-hidden className="animate-spin" />}
              {view.set ? "Replace" : "Save"}
            </Button>
            {view.set && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void run(() => actions.clear(view.name))}
              >
                Clear
              </Button>
            )}
          </div>
          {isAddress && (
            <p className="text-xs text-muted-foreground">
              {view.carries &&
                `A new or cleared address clears ${view.carries} and switches the plugin off. `}
              From the dashboard an address must be https and reach public addresses only; set a
              private or http one in .env.
            </p>
          )}
        </form>
      )}
      <SaveError problems={problems} />
    </div>
  );
}

/**
 * Rows for `views`: each address followed by the token it carries, which has no input until the
 * address is set. A token typed for an address is dropped when the address changes.
 */
export function SecretFields({ views, actions }: { views: SecretView[]; actions: SecretActions }) {
  const addressOf = new Map(
    views.filter((v) => v.carries).map((v) => [v.carries as string, v] as const),
  );
  const ordered = views
    .filter((v) => !addressOf.has(v.name))
    .flatMap((v) => [v, ...views.filter((t) => t.name === v.carries)]);
  return (
    <div className="flex flex-col gap-3">
      {ordered.map((view) => (
        <SecretField
          key={view.name}
          view={view}
          address={addressOf.get(view.name)}
          actions={actions}
        />
      ))}
    </div>
  );
}
