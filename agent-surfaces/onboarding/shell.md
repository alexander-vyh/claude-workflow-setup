# Non-Interactive Shell Commands

Nothing may block on a prompt. Use `ssh -o BatchMode=yes` and
`scp -o BatchMode=yes`. Environment defaults that avoid prompts are set for you
in the Escapement settings `env` block rather than repeated here.
