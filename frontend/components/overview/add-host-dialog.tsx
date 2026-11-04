"use client";

import { Plus } from "lucide-react";
import { useState } from "react";

import { SaveLockProvider, useSaveLock } from "@/components/save-lock";
import { HostForm, SSH_ONLY } from "@/components/settings/host-form";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

function AddHost({
  onAdded,
  trigger,
}: {
  onAdded: () => Promise<void>;
  trigger: React.ReactElement;
}) {
  const [open, setOpen] = useState(false);
  const { busy } = useSaveLock();
  return (
    <Dialog
      open={open}
      // Neither Esc, the backdrop nor the close button drops a save that is in flight.
      onOpenChange={(next) => {
        if (next || !busy) setOpen(next);
      }}
    >
      <DialogTrigger render={trigger} />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a host</DialogTitle>
          <DialogDescription>{SSH_ONLY}</DialogDescription>
        </DialogHeader>
        <HostForm onChanged={onAdded} onClose={() => setOpen(false)} />
      </DialogContent>
    </Dialog>
  );
}

/**
 * Opens the add-host form in a dialog from `trigger`. The form unmounts when the dialog closes,
 * so a typed key does not outlive it; `onAdded` reads the hosts again after a save.
 */
export function AddHostDialog(props: {
  onAdded: () => Promise<void>;
  trigger: React.ReactElement;
}) {
  return (
    <SaveLockProvider>
      <AddHost {...props} />
    </SaveLockProvider>
  );
}

/** The round + at the end of the "Your hosts" row. */
export function AddHostButton({ onAdded }: { onAdded: () => Promise<void> }) {
  return (
    <AddHostDialog
      onAdded={onAdded}
      trigger={
        <Button
          variant="ghost"
          size="icon"
          aria-label="Add a host"
          title="Add a host"
          className="-my-2 shrink-0 bg-accent/12 text-accent hover:bg-accent/20 [&_svg]:size-5"
        >
          <Plus aria-hidden />
        </Button>
      }
    />
  );
}
