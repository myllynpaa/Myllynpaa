"""
Yksinkertainen paikallinen nettisivu kerättyjen johdon liiketoimet
-tiedotteiden katseluun taulukkona.

Käyttö:
    pip3 install flask requests beautifulsoup4
    python3 webapp.py

Avaa sitten selaimessa: http://127.0.0.1:5000
"""

from __future__ import annotations

from datetime import date, timedelta

from flask import Flask, render_template_string, request

from nasdaq_api_collector import collect

app = Flask(__name__)

PAGE = """
<!doctype html>
<html lang="fi">
<head>
<meta charset="utf-8">
<title>Sisäpiirikaupat</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 2rem; background: #f7f7f8; color: #1a1a1a; }
  h1 { margin-bottom: 0.25rem; }
  form { margin-bottom: 1.5rem; }
  table { border-collapse: collapse; width: 100%; background: white; }
  th, td { border: 1px solid #ddd; padding: 0.5rem 0.75rem; text-align: left; font-size: 0.9rem; }
  th { background: #eee; }
  tr.buy { background: #eafaf1; }
  tr.sell { background: #fdecea; }
  .status-OK { color: #2e7d32; }
  .status-NEEDS_REVIEW { color: #b8860b; }
  .status-UNPARSED { color: #c62828; }
  .error { color: #c62828; font-weight: bold; }
  .muted { color: #666; font-size: 0.85rem; }
</style>
</head>
<body>
  <h1>Sisäpiirikaupat</h1>
  <p class="muted">Nasdaq Helsinki + First North — johdon liiketoimet -tiedotteet (MAR 19)</p>
  <form method="get">
    <label>Alkaen: <input type="date" name="from_date" value="{{ from_date }}"></label>
    <button type="submit">Hae tiedotteet</button>
  </form>

  {% if error %}
    <p class="error">Virhe haussa: {{ error }}</p>
  {% elif rows %}
    <p>{{ rows|length }} riviä, {{ release_count }} tiedotetta.</p>
    <table>
      <tr>
        <th>Yhtiö</th><th>Henkilö</th><th>Asema</th><th>Pvm</th>
        <th>Laji</th><th>Volyymi</th><th>Hinta</th><th>Valuutta</th><th>Arvo</th><th>Tila</th>
      </tr>
      {% for r in rows %}
      <tr class="{{ 'buy' if r.kind == 'BUY' else ('sell' if r.kind == 'SELL' else '') }}">
        <td>{{ r.issuer_name or '' }}</td>
        <td>{{ r.person_display or r.person_name or '' }}</td>
        <td>{{ r.role }}</td>
        <td>{{ r.transaction_date or '' }}</td>
        <td>{{ r.kind }}</td>
        <td>{{ r.volume }}</td>
        <td>{{ r.price }}</td>
        <td>{{ r.currency or '' }}</td>
        <td>{{ r.value }}</td>
        <td class="status-{{ r.status }}">{{ r.status }}</td>
      </tr>
      {% endfor %}
    </table>
  {% elif searched %}
    <p>Ei tuloksia tältä väliltä.</p>
  {% endif %}
</body>
</html>
"""


@app.route("/")
def index():
    from_date_str = request.args.get("from_date")
    searched = from_date_str is not None
    if not from_date_str:
        from_date_str = (date.today() - timedelta(days=14)).isoformat()

    rows = []
    error = None
    release_count = 0
    if searched:
        try:
            from_date = date.fromisoformat(from_date_str)
            collected = collect(from_date)
            release_count = len(collected)
            for entry in collected:
                rows.extend(entry["transactions"])
        except Exception as exc:  # näytetään virhe sivulla terminaalin sijaan
            error = str(exc)

    return render_template_string(
        PAGE,
        from_date=from_date_str,
        rows=rows,
        error=error,
        searched=searched,
        release_count=release_count,
    )


if __name__ == "__main__":
    print("Avaa selaimessa: http://127.0.0.1:5000")
    app.run(debug=False, port=5000)
