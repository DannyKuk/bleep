/** A message with `code` spans: the text between backticks is shown in mono, so a translator can
 * place a command or file name where the language wants it. */
export function CodeText({ text }: { text: string }) {
  return (
    <>
      {text.split("`").map((part, index) =>
        index % 2 === 1 ? (
          <code key={index} className="font-mono">
            {part}
          </code>
        ) : (
          part
        ),
      )}
    </>
  );
}
