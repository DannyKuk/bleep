#!/usr/bin/env bash
# Sets an issue's Status on the "Bleep roadmap" board (project 4), adding the issue first if
# it isn't on the board. Done is set by GitHub when the issue closes.
#   scripts/board.sh <issue#> <Backlog|Ready|In progress|In review>
set -euo pipefail

PROJECT_ID=PVT_kwHOBqTJc84Bp3Ce
STATUS_FIELD_ID=PVTSSF_lAHOBqTJc84Bp3CezhoQr5k

if [[ $# -ne 2 ]]; then
  echo "usage: scripts/board.sh <issue#> <Backlog|Ready|In progress|In review>" >&2
  exit 2
fi

issue=$1
case $2 in
  Backlog) option=1f80bbf2 ;;
  Ready) option=16e58691 ;;
  "In progress") option=558255ae ;;
  "In review") option=50c58ad9 ;;
  *) echo "unknown status '$2': use Backlog, Ready, In progress or In review" >&2; exit 2 ;;
esac

item=$(gh api graphql -F number="$issue" -f query='
  query($number: Int!) {
    repository(owner: "DannyKuk", name: "bleep") {
      issue(number: $number) { projectItems(first: 20) { nodes { id project { id } } } }
    }
  }' --jq ".data.repository.issue.projectItems.nodes[] | select(.project.id == \"$PROJECT_ID\") | .id")

if [[ -z $item ]]; then
  item=$(gh project item-add 4 --owner DannyKuk \
    --url "https://github.com/DannyKuk/bleep/issues/$issue" --format json --jq .id)
fi

gh project item-edit --project-id "$PROJECT_ID" --id "$item" \
  --field-id "$STATUS_FIELD_ID" --single-select-option-id "$option" > /dev/null
echo "#$issue -> $2"
