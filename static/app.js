(function () {
  const form = document.getElementById('trackForm');
  const status = document.getElementById('status');
  const btn = document.getElementById('submitBtn');
  const lo = document.getElementById('min_price');
  const hi = document.getElementById('max_price');
  const loLabel = document.getElementById('loLabel');
  const hiLabel = document.getElementById('hiLabel');
  const bandText = document.getElementById('bandText');
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const inr = (v) => '₹' + Number(v).toLocaleString('en-IN');

  function updateBand() {
    const a = parseFloat(lo.value), b = parseFloat(hi.value);
    if (!isNaN(a) && !isNaN(b) && a <= b) {
      loLabel.textContent = inr(a);
      hiLabel.textContent = inr(b);
      bandText.textContent = 'You will be emailed when the price changes and is between ' + inr(a) + ' and ' + inr(b) + '.';
    } else {
      loLabel.textContent = 'your lowest';
      hiLabel.textContent = 'your highest';
      bandText.textContent = 'Alerts fire only while the price is between your two numbers.';
    }
  }
  lo.addEventListener('input', updateBand);
  hi.addEventListener('input', updateBand);

  function show(msg, ok) {
    status.textContent = msg;
    status.className = 'status ' + (ok ? 'ok' : 'err');
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!form.checkValidity()) { form.reportValidity(); return; }
    btn.disabled = true;
    show('Sending…', true);
    try {
      const res = await fetch('/api/track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) { show(data.message, true); form.reset(); updateBand(); }
      else show(data.error || 'Something went wrong. Try again.', false);
    } catch (_) {
      show('Could not reach the server. Check your connection.', false);
    } finally {
      btn.disabled = false;
    }
  });
})();
