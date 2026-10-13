// Template for the review Workflow. Copy it to the run's directory, replace only LENSES with
// prompts written for THIS diff, and run the copy by scriptPath with args:
//   {wt, diff, base, head, title, scratch, testCommand, tier, previous}
// wt: the review worktree; scratch: a directory agents may create their own worktrees under;
// testCommand: what the baseline ran ('' when the project has no runnable suite);
// tier: 'balanced' (default) or 'max' (every agent on the session model);
// previous: earlier findings, ONLY on a re-review, ONLY for the Reconcile phase.
export const meta = {
  name: 'vet',
  description: 'Review an MR/PR through diff-derived lenses, dedup, verify, prove by running, mutate',
  phases: [
    { title: 'Review', detail: 'lenses read the diff; the mutation pass starts in the run queue' },
    { title: 'Dedup' },
    { title: 'Verify', detail: 'correctness gate, then relevance' },
    { title: 'Prove', detail: 'blockers and should-fix reproduced by a command' },
    { title: 'Reconcile', detail: 're-review only: earlier findings re-run at this tip' },
  ],
}

// Cheap where the output is checked by running or by mechanics; the session model where a
// wrong "refuted" would kill a real defect.
const MAX = args.tier === 'max'
const tier = (model, effort) => (MAX ? {} : { ...(model ? { model } : {}), ...(effort ? { effort } : {}) })
const STRONG = tier()
const MID = tier('sonnet')
const MID_LOW = tier('sonnet', 'low')
const CHEAP = tier('haiku', 'low')

const COMMON = `You are reviewing: ${args.title}.
The head ${args.head} is checked out at ${args.wt}. Treat that worktree as read-only, and do not run the test suite, a build or anything heavy: read and grep. Runs belong to the prove and mutation steps, which take turns.
Merge base: ${args.base}. Full diff: ${args.diff}.
Judge against the repository's CLAUDE.md, the docs it links to, and its own review skill if one exists (severity: blocker / should-fix / nit).
Do not invent conventions the repository does not write down.
Read the surrounding code, callers and installed libraries, not just the diff, and read deleted lines too: a removed guard or comment is a reversed decision.
Every finding needs: file (repo-relative); line (in the HEAD file); in_diff (true if that line is an added line); a concrete failure scenario; a concrete fix; evidence.
Report only real, specific issues. An empty list is fine.`

const OWN_WORKTREE = (label) => `You are a run step: you may run tests, builds and scripts, but only inside your own checkout. Create it and never touch ${args.wt}:
  git -C ${args.wt} worktree add --detach ${args.scratch}/wt-${label} ${args.head}
Work only inside it and remove it when done:
  git -C ${args.wt} worktree remove --force ${args.scratch}/wt-${label}`

// One entry per lens. Each prompt names files, functions and scenarios to trace for THIS diff.
// `opts` picks the tier; keep the core lenses STRONG.
const LENSES = [
  { key: 'core-correctness', opts: STRONG, prompt: 'LENS: ...' },
  { key: 'contract-consumers', opts: STRONG, prompt: 'LENS: grep every caller of whatever contract changed ...' },
  { key: 'lifecycle-concurrency', opts: STRONG, prompt: 'LENS: ...' },
  { key: 'config-docs-deploy', opts: MID, prompt: 'LENS: env defaults, deploy templates, docs and comments the diff made false, the changelog ...' },
  { key: 'tests', opts: MID, prompt: 'LENS: does each test fail without the change; does a test name promise what its body asserts; patches of things actually called; missing cases ...' },
  { key: 'quality-scope', opts: MID, prompt: 'LENS: repo comment rules, noqa/type-ignore without a reason on the line, redundant code, out-of-scope files, a request body silent about a behaviour change ...' },
  {
    key: 'mechanical', opts: CHEAP,
    prompt: 'LENS: grep, do not reason. In the changed files and their direct neighbours find: code no path reaches; a suppression with no reason on the line; a doc, comment or docstring naming something the diff renamed or removed (grep the OLD name and the old phrase); a number, date, version or link that one place changed and another did not. Every finding quotes the grep and its output as evidence.',
  },
]

const FINDING = {
  type: 'object',
  properties: {
    title: { type: 'string' },
    severity: { type: 'string', enum: ['blocker', 'should-fix', 'nit'] },
    file: { type: 'string' },
    line: { type: 'integer' },
    in_diff: { type: 'boolean' },
    scenario: { type: 'string' },
    fix: { type: 'string' },
    evidence: { type: 'string' },
  },
  required: ['title', 'severity', 'file', 'line', 'in_diff', 'scenario', 'fix', 'evidence'],
}
const FINDINGS = {
  type: 'object',
  properties: { findings: { type: 'array', items: FINDING }, checked_ok: { type: 'array', items: { type: 'string' } } },
  required: ['findings', 'checked_ok'],
}
const MERGED = {
  type: 'object',
  properties: {
    groups: {
      type: 'array',
      items: {
        type: 'object',
        properties: { keep: { type: 'integer' }, merged: { type: 'array', items: { type: 'integer' } }, note: { type: 'string' } },
        required: ['keep', 'merged', 'note'],
      },
    },
  },
  required: ['groups'],
}
const CORRECT = {
  type: 'object',
  properties: {
    refuted: { type: 'boolean' },
    severity: { type: 'string', enum: ['blocker', 'should-fix', 'nit'] },
    corrected_line: { type: 'integer' },
    reasoning: { type: 'string' },
  },
  required: ['refuted', 'severity', 'corrected_line', 'reasoning'],
}
const RELEVANT = {
  type: 'object',
  properties: { relevant: { type: 'boolean' }, reasoning: { type: 'string' } },
  required: ['relevant', 'reasoning'],
}
const PROOF = {
  type: 'object',
  properties: {
    reproduced: { type: 'boolean' },
    command: { type: 'string' },
    output: { type: 'string' },
    notes: { type: 'string' },
  },
  required: ['reproduced', 'command', 'output', 'notes'],
}
const MUTATIONS = {
  type: 'object',
  properties: {
    baseline: { type: 'string' },
    mutations: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          behaviour: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'integer' },
          applied_diff: { type: 'string' },
          result: { type: 'string', enum: ['green', 'red', 'not-applied'] },
          output_tail: { type: 'string' },
        },
        required: ['behaviour', 'file', 'line', 'applied_diff', 'result', 'output_tail'],
      },
    },
  },
  required: ['baseline', 'mutations'],
}
const RECONCILED = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['still-open', 'closed', 'withdrawn'] },
    command: { type: 'string' },
    output: { type: 'string' },
    explanation: { type: 'string' },
  },
  required: ['status', 'command', 'output', 'explanation'],
}

// Every agent that runs commands goes through this queue, one at a time: several test suites
// at once in several worktrees have hung the machine. Reading agents stay parallel.
let runQueue = Promise.resolve()
const serially = (start) => {
  const next = runQueue.then(start, start)
  runQueue = next.catch(() => null)
  return next
}

phase('Review')
const mutationRun = args.testCommand
  ? serially(() => agent(
      `${COMMON}\n\n${OWN_WORKTREE('mutation')}\n\nMUTATION PASS. Test command: ${args.testCommand}\n` +
        `Run it once unmodified and record the summary line as baseline. Then, for each BEHAVIOUR the diff added ` +
        `(a branch of a conditional, a guard, a filter clause, an element of a sort key, a validation; not plumbing), ` +
        `delete or neutralise that one thing, print \`git diff --stat\` and the changed lines, run the suite, record the result, ` +
        `then restore YOUR worktree with \`git reset --hard -q && git clean -fdq\` (that also removes files a run created) and confirm ` +
        `\`git status --porcelain\` prints nothing before the next one. One mutation per run. ` +
        `An empty diff --stat means the mutation did not apply: record not-applied, never green. At most 12 mutations, the riskiest first.`,
      { label: 'mutation', phase: 'Review', schema: MUTATIONS, ...MID },
    ))
  : Promise.resolve(null)
const reviews = await parallel(
  LENSES.map((l) => () =>
    agent(`${COMMON}\n\n${l.prompt}`, { label: `review:${l.key}`, phase: 'Review', schema: FINDINGS, ...l.opts }).then(
      (r) => r && { lens: l.key, ...r },
    ),
  ),
)
const ok = reviews.filter(Boolean)
if (ok.length < LENSES.length) log(`${LENSES.length - ok.length} lenses returned nothing (skipped or died)`)
const raw = ok.flatMap((r) => r.findings.map((f) => ({ ...f, lens: r.lens })))
const checkedOk = ok.flatMap((r) => r.checked_ok.map((s) => `[${r.lens}] ${s}`))
log(`${raw.length} raw findings from ${ok.length}/${LENSES.length} lenses`)

// Barrier on purpose: duplicates must merge before verification, or one defect gets two verdicts.
phase('Dedup')
const listing = raw.map((f, i) => `${i}. [${f.lens}] ${f.file}:${f.line} ${f.title}`).join('\n')
const dedup = raw.length > 1
  ? await agent(
      `Group findings that describe the SAME defect (same root cause), even if lenses or lines differ. ` +
        `When unsure, keep them apart: a wrong merge loses a finding, a missed merge only costs one verification.\n` +
        `Every index appears in exactly one group. keep = the most complete one.\n\n${listing}`,
      { label: 'dedup', phase: 'Dedup', schema: MERGED, ...CHEAP },
    )
  : { groups: [] }
const seen = new Set()
const unique = []
for (const g of (dedup && dedup.groups) || []) {
  if (seen.has(g.keep) || !raw[g.keep]) continue
  ;[g.keep, ...g.merged].forEach((i) => seen.add(i))
  unique.push({ ...raw[g.keep], also: g.merged.filter((i) => i !== g.keep).map((i) => raw[i]).filter(Boolean) })
}
raw.forEach((f, i) => { if (!seen.has(i)) unique.push({ ...f, also: [] }) })
log(`${unique.length} unique findings after dedup`)

// Correctness is the gate; relevance can only remove. A relevance "yes" never outvotes a refutation.
const verifyAndProve = await pipeline(
  unique,
  (f) => {
    const serious = f.severity !== 'nit'
    const correctness = agent(
      `${COMMON}\n\nADVERSARIALLY VERIFY this finding for CORRECTNESS: trace the actual code and try to refute it. ` +
        `Default to refuted=true if the claim is unsupported. Open the file and give the correct HEAD line.\n\nFINDING: ${JSON.stringify(f)}`,
      { label: `correct:${f.file.split('/').pop()}:${f.line}`, phase: 'Verify', schema: CORRECT, ...(serious ? STRONG : MID_LOW) },
    )
    const relevance = agent(
      `${COMMON}\n\nJudge RELEVANCE only: is this within the repository's written conventions and worth a review comment, ` +
        `or noise, out of scope, or handled elsewhere? Assume the claim is true.\n\nFINDING: ${JSON.stringify(f)}`,
      { label: `relevant:${f.file.split('/').pop()}:${f.line}`, phase: 'Verify', schema: RELEVANT, ...MID },
    )
    return Promise.all([correctness, relevance]).then(([c, r]) => {
      // A verifier that died is not a refutation: the finding stays, as a question, and is logged.
      if (!c) log(`correctness verifier returned nothing for ${f.file}:${f.line}; kept as a question`)
      if (!r) log(`relevance verifier returned nothing for ${f.file}:${f.line}; treated as relevant`)
      return {
        ...f,
        correctness: c,
        relevance: r,
        unverified: !c,
        severity: !c ? 'question' : !c.refuted ? c.severity : f.severity,
        line: c && c.corrected_line ? c.corrected_line : f.line,
        survives: !c || (!c.refuted && (!r || r.relevant)),
      }
    })
  },
  (f, _item, i) => {
    if (!f.survives || f.severity === 'nit' || f.severity === 'question') return f
    return serially(() => agent(
      `${COMMON}\n\n${OWN_WORKTREE(`prove-${i}`)}\n\nREPRODUCE this finding by RUNNING something: a failing test you write, ` +
        `a short script, the build, a grep that comes back non-empty, two runs that disagree. Paste the command and its output VERBATIM. ` +
        `If nothing you can run shows it, say reproduced=false and why; do not argue it from reading.\n\nFINDING: ${JSON.stringify(f)}`,
      { label: `prove:${f.file.split('/').pop()}:${f.line}`, phase: 'Prove', schema: PROOF, ...MID },
    )).then((p) => {
      if (!p) log(`prover returned nothing for ${f.file}:${f.line}; kept as a question`)
      return { ...f, proof: p, severity: p && p.reproduced ? f.severity : 'question' }
    })
  },
)

let reconciled = []
if (Array.isArray(args.previous) && args.previous.length) {
  phase('Reconcile')
  reconciled = await parallel(
    args.previous.map((p, i) => () =>
      serially(() => agent(
        `${COMMON}\n\n${OWN_WORKTREE(`reconcile-${i}`)}\n\nAn EARLIER review round claimed the finding below. Re-run its reproduction ` +
          `against THIS head, from scratch; never reuse its old output. still-open: it reproduces today (paste today's output). ` +
          `closed: it no longer reproduces; name the commit and file:line that closed it. withdrawn: it was wrong, or names a path, ` +
          `symbol or flag that does not exist; say what is actually true.\n\nEARLIER FINDING: ${JSON.stringify(p)}`,
        { label: `reconcile:${i}`, phase: 'Reconcile', schema: RECONCILED, ...MID },
      )).then((v) => v && { previous: p, ...v }),
    ),
  )
}

const mutation = await mutationRun
return {
  findings: verifyAndProve.filter(Boolean),
  checked_ok: checkedOk,
  mutation,
  reconciled: reconciled.filter(Boolean),
}
