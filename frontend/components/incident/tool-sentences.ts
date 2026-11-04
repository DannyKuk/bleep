// What each of Bleep's read tools did, as Bleep says it. The sentences never quote the call's
// arguments: the model chose them, and they could carry text from a hostile log, which must not
// read as Bleep's own words. The arguments stay beside the sentence in mono, as data.
const SENTENCES: Record<string, string> = {
  list_containers: "I listed the containers on the host",
  inspect_container: "I looked closely at a container",
  container_logs: "I read a container's logs",
  container_stats: "I checked a container's CPU and memory",
  recent_events: "I looked at the host's recent Docker events",
  host_command: "I ran a read-only check on the host",
  recent_commits: "I looked at recent commits",
  commit_diff: "I read what a commit changed",
  knowledge_search: "I looked it up in the docs",
  recall_incidents: "I looked for incidents like this one",
  coolify_applications: "I listed the Coolify applications",
  coolify_deployments: "I looked at an application's deployments",
  coolify_env_keys: "I looked at which variables an application sets",
  mcp_tools: "I listed what the MCP server offers",
  mcp_call: "I asked the MCP server",
};

/** The plain sentence for a known tool, or null for a tool the dashboard has no words for. */
export function toolSentence(name: string): string | null {
  return Object.hasOwn(SENTENCES, name) ? SENTENCES[name] : null;
}
