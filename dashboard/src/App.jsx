import { useEffect, useMemo, useState } from 'react'

function formatList(value) {
  if (!Array.isArray(value) || value.length === 0) return 'None'
  return value.join(', ')
}

function formatTimestamp(value) {
  if (!value) return 'n/a'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString()
}

function formatDuration(startedAt, finishedAt, running) {
  if (!startedAt) return 'n/a'
  const start = new Date(startedAt).getTime()
  const end = running ? Date.now() : finishedAt ? new Date(finishedAt).getTime() : Date.now()
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return 'n/a'
  const totalSeconds = Math.floor((end - start) / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}m ${seconds}s`
}

function StatusPill({ passed }) {
  return (
    <span className={`pill ${passed ? 'pill-success' : 'pill-failed'}`}>
      {passed ? 'Passed' : 'Failed'}
    </span>
  )
}

function Section({ title, subtitle, children }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>{title}</h2>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
      </div>
      {children}
    </section>
  )
}

export default function App() {
  const [report, setReport] = useState(null)
  const [testCode, setTestCode] = useState('Loading generated test code...')
  const [runState, setRunState] = useState(null)
  const [isRunning, setIsRunning] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true

    async function loadData() {
      try {
        const [reportResponse, testCodeResponse] = await Promise.all([
          fetch('/api/report'),
          fetch('/api/test-code'),
        ])
        const runStatusResponse = await fetch('/api/run-status')

        if (!reportResponse.ok) throw new Error('Unable to load report.json')
        if (!testCodeResponse.ok) throw new Error('Unable to load generated test code')
        if (!runStatusResponse.ok) throw new Error('Unable to load run status')

        const [reportJson, testCodeText] = await Promise.all([
          reportResponse.json(),
          testCodeResponse.text(),
        ])
        const runStatusJson = await runStatusResponse.json()

        if (!active) return
        setReport(reportJson)
        setTestCode(testCodeText)
        setRunState(runStatusJson)
        setError('')
      } catch (exception) {
        if (!active) return
        setError(exception instanceof Error ? exception.message : String(exception))
      } finally {
        if (active) setLoading(false)
      }
    }

    loadData()
    const intervalId = window.setInterval(loadData, 5000)
    return () => {
      active = false
      window.clearInterval(intervalId)
    }
  }, [])

  useEffect(() => {
    if (!runState?.running) return undefined

    let active = true
    const intervalId = window.setInterval(async () => {
      try {
        const response = await fetch('/api/run-status')
        if (!response.ok || !active) return
        setRunState(await response.json())
      } catch {
        // Keep UI usable even if one poll fails.
      }
    }, 1200)

    return () => {
      active = false
      window.clearInterval(intervalId)
    }
  }, [runState?.running])

  async function runPipeline() {
    setIsRunning(true)
    setError('')
    try {
      const response = await fetch('/api/run', { method: 'POST' })
      if (!response.ok && response.status !== 202) {
        throw new Error('Pipeline could not be started')
      }

      const statusResponse = await fetch('/api/run-status')
      if (statusResponse.ok) {
        setRunState(await statusResponse.json())
      }
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : String(exception))
    } finally {
      setIsRunning(false)
    }
  }

  const metrics = report?.metrics ?? {}
  const testRun = report?.test_run ?? {}
  const predictiveSelection = report?.predictive_selection ?? {}
  const pipelineEvents = useMemo(() => report?.pipeline_events ?? [], [report])
  const durationLabel = formatDuration(runState?.startedAt, runState?.finishedAt, runState?.running)

  return (
    <div className="app-shell">
      <header className="hero">
        <div>
          <span className="eyebrow">AI Test Generator</span>
          <h1>Pipeline Dashboard</h1>
          <p>
            Generated tests, predictive selection, self-healing attempts, and live run
            status in one place.
          </p>
        </div>
        <div className="hero-card">
          <div className="hero-label">Overall Status</div>
          <div className="hero-value">
            {loading ? 'Loading...' : <StatusPill passed={Boolean(metrics.passed)} />}
          </div>
          <div className="hero-meta">Auto-refreshes every 5 seconds.</div>
          <button className="run-button" onClick={runPipeline} disabled={isRunning}>
            {isRunning ? 'Running pipeline...' : 'Run pipeline'}
          </button>
          <div className="hero-meta">Live run status: {runState?.message ?? 'Idle'}</div>
        </div>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}

      <main className="grid">
        <Section title="Run Summary" subtitle="Latest pipeline snapshot from report.json.">
          <div className="stats">
            <div className="stat">
              <span>Functions</span>
              <strong>{metrics.functions ?? '0'}</strong>
            </div>
            <div className="stat">
              <span>Classes</span>
              <strong>{metrics.classes ?? '0'}</strong>
            </div>
            <div className="stat">
              <span>Heal Attempts</span>
              <strong>{report?.heal_attempts ?? 0}</strong>
            </div>
            <div className="stat">
              <span>Test File</span>
              <strong>{report?.test_file ?? 'Unknown'}</strong>
            </div>
            <div className="stat">
              <span>Dashboard Run</span>
              <strong>{runState?.running ? 'Running' : 'Idle'}</strong>
            </div>
          </div>
        </Section>

        <Section title="Predictive Selection" subtitle="What the agent chose to run.">
          <div className="detail-list">
            <div>
              <span>Enabled</span>
              <strong>{predictiveSelection.enabled ? 'Yes' : 'No'}</strong>
            </div>
            <div>
              <span>Base Ref</span>
              <strong>{predictiveSelection.base_ref ?? 'HEAD~1'}</strong>
            </div>
            <div>
              <span>Changed Files</span>
              <strong>{formatList(predictiveSelection.changed_files)}</strong>
            </div>
            <div>
              <span>Selected Tests</span>
              <strong>{formatList(predictiveSelection.selected_tests)}</strong>
            </div>
          </div>
        </Section>

        <Section title="Job Details" subtitle="Dashboard-triggered run diagnostics.">
          <div className="detail-list">
            <div>
              <span>Started</span>
              <strong>{formatTimestamp(runState?.startedAt)}</strong>
            </div>
            <div>
              <span>Finished</span>
              <strong>{formatTimestamp(runState?.finishedAt)}</strong>
            </div>
            <div>
              <span>Exit Code</span>
              <strong>{runState?.exitCode ?? 'n/a'}</strong>
            </div>
            <div>
              <span>Duration</span>
              <strong>{durationLabel}</strong>
            </div>
          </div>
        </Section>

        <Section title="Pipeline Timeline" subtitle="Recorded stage-by-stage events.">
          <div className="timeline">
            {pipelineEvents.length === 0 ? (
              <div className="empty-state">No events recorded yet.</div>
            ) : (
              pipelineEvents.map((event, index) => (
                <div key={`${event.timestamp_utc}-${index}`} className="timeline-item">
                  <div className="timeline-dot" />
                  <div>
                    <div className="timeline-top">
                      <strong>{event.stage}</strong>
                      <span>{event.status}</span>
                    </div>
                    <p>{event.message}</p>
                    <small>{event.timestamp_utc}</small>
                  </div>
                </div>
              ))
            )}
          </div>
        </Section>

        <Section title="Pytest Output" subtitle="Latest runner output from the pipeline.">
          <pre className="code-block">{runState?.output || testRun.output || 'No output yet.'}</pre>
        </Section>

        <Section title="Generated Test Code" subtitle="Current contents of tests/test_generated.py.">
          <pre className="code-block code-large">{testCode}</pre>
        </Section>

        <Section title="Healing Details" subtitle="What happened during self-healing.">
          <pre className="code-block">{JSON.stringify(report?.heal_history ?? [], null, 2)}</pre>
        </Section>
      </main>

      <footer className="footer">Report timestamp: {report?.timestamp_utc ?? 'n/a'}</footer>
    </div>
  )
}