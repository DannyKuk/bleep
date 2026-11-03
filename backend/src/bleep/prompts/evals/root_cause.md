The evaluation steps the root cause judge (DeepEval GEval) follows. Each line that starts with "- " is one step; the rest of this file is not sent.

- Read the expected output: the root cause of the incident, in one or two sentences, as the person who caused the fault knows it.
- Read the actual output: the diagnosis an on-call agent wrote from the containers, logs and checks it looked at, with its category and evidence.
- Decide whether the diagnosis names the same root cause: the same failing component and the same reason it fails. A symptom of the cause (a crashing container, a failing URL, a 500 answer) is not the cause.
- Do not reward a diagnosis for listing several possible causes when it does not commit to the expected one, and do not penalise it for extra detail that is correct.
- Give a high score when the root cause matches, a middling score when it names the right component but a wrong or vague reason, and a low score when it names another cause.
