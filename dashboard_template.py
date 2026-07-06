"""
dashboard_template.py

HTML template for the Stretch 3 Analytics Dashboard (GET /dashboard).
Kept separate from app.py for readability. Uses simple CSS bar
visualizations (width % driven by computed percentages) rather than a
charting library, keeping the stretch feature dependency-free.
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Provenance Guard — Analytics Dashboard</title>
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f1115;
    color: #e6e6e6;
    margin: 0;
    padding: 40px;
  }
  h1 {
    font-size: 1.5rem;
    margin-bottom: 4px;
  }
  .subtitle {
    color: #888;
    font-size: 0.85rem;
    margin-bottom: 32px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 24px;
  }
  .card {
    background: #1a1d24;
    border: 1px solid #2a2e38;
    border-radius: 10px;
    padding: 20px 24px;
  }
  .card h2 {
    font-size: 0.95rem;
    color: #a8b3cf;
    margin: 0 0 16px 0;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .big-number {
    font-size: 2.2rem;
    font-weight: 600;
    color: #fff;
  }
  .bar-row {
    display: flex;
    align-items: center;
    margin: 10px 0;
    font-size: 0.85rem;
  }
  .bar-label {
    width: 90px;
    flex-shrink: 0;
    color: #ccc;
  }
  .bar-track {
    flex-grow: 1;
    background: #2a2e38;
    border-radius: 4px;
    height: 16px;
    overflow: hidden;
    margin: 0 10px;
  }
  .bar-fill {
    height: 100%;
    border-radius: 4px;
  }
  .bar-fill.ai { background: #e05c5c; }
  .bar-fill.human { background: #5ce0a0; }
  .bar-fill.uncertain { background: #e0c15c; }
  .bar-fill.agree { background: #5c9ee0; }
  .bar-fill.disagree { background: #b05ce0; }
  .bar-value {
    width: 50px;
    text-align: right;
    color: #ccc;
    flex-shrink: 0;
  }
  .footnote {
    color: #777;
    font-size: 0.75rem;
    margin-top: 10px;
  }
</style>
</head>
<body>
  <h1>Provenance Guard — Analytics Dashboard</h1>
  <div class="subtitle">Generated at {{ generated_at }}</div>

  <div class="grid">

    <div class="card">
      <h2>Total Submissions</h2>
      <div class="big-number">{{ total_submissions }}</div>
    </div>

    <div class="card">
      <h2>Detection Patterns</h2>
      <div class="bar-row">
        <div class="bar-label">AI</div>
        <div class="bar-track"><div class="bar-fill ai" style="width: {{ pct_ai }}%"></div></div>
        <div class="bar-value">{{ pct_ai }}%</div>
      </div>
      <div class="bar-row">
        <div class="bar-label">Human</div>
        <div class="bar-track"><div class="bar-fill human" style="width: {{ pct_human }}%"></div></div>
        <div class="bar-value">{{ pct_human }}%</div>
      </div>
      <div class="bar-row">
        <div class="bar-label">Uncertain</div>
        <div class="bar-track"><div class="bar-fill uncertain" style="width: {{ pct_uncertain }}%"></div></div>
        <div class="bar-value">{{ pct_uncertain }}%</div>
      </div>
      <div class="footnote">{{ count_ai }} ai / {{ count_human }} human / {{ count_uncertain }} uncertain</div>
    </div>

    <div class="card">
      <h2>Appeal Rate</h2>
      <div class="big-number">{{ appeal_rate_pct }}%</div>
      <div class="footnote">{{ total_appeals }} appeal(s) out of {{ total_submissions }} submission(s)</div>
    </div>

    <div class="card">
      <h2>Signal Agreement Rate</h2>
      <div class="bar-row">
        <div class="bar-label">Agree</div>
        <div class="bar-track"><div class="bar-fill agree" style="width: {{ agreement_rate_pct }}%"></div></div>
        <div class="bar-value">{{ agreement_rate_pct }}%</div>
      </div>
      <div class="bar-row">
        <div class="bar-label">Disagree</div>
        <div class="bar-track"><div class="bar-fill disagree" style="width: {{ disagreement_rate_pct }}%"></div></div>
        <div class="bar-value">{{ disagreement_rate_pct }}%</div>
      </div>
      <div class="footnote">
        {{ agree_count }} agree / {{ disagree_count }} disagree
        ({{ excluded_no_field_count }} excluded — predate agreement tracking)
      </div>
    </div>

  </div>

  <p class="footnote" style="margin-top: 32px;">
    Data source: audit_log.json, computed live on each request. See
    also <a href="/analytics" style="color:#5c9ee0;">GET /analytics</a> for raw JSON.
  </p>
</body>
</html>
"""
