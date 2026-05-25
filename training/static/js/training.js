/* Training module client logic — quiz submit + result render. */

(function () {
  const form = document.getElementById('training-quiz-form');
  if (!form) return;

  const cfg = window.TRAINING_QUIZ;
  const resultEl = document.getElementById('training-quiz-result');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const answers = {};
    let unanswered = 0;
    form.querySelectorAll('.training-question').forEach(q => {
      const qid = q.dataset.qid;
      const sel = q.querySelector(`input[name="q-${qid}"]:checked`);
      if (!sel) { unanswered++; return; }
      answers[qid] = parseInt(sel.value, 10);
    });
    if (unanswered > 0) {
      alert(`Please answer all questions — ${unanswered} remaining.`);
      return;
    }

    const submitBtn = form.querySelector('button[type=submit]');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Grading…';

    try {
      const res = await fetch(cfg.submit_url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({answers})
      }).then(r => r.json());

      if (!res.ok) {
        alert('Submit failed: ' + (res.error || 'unknown'));
        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit answers';
        return;
      }

      renderResult(res.data);
    } catch (err) {
      alert('Network error: ' + err.message);
      submitBtn.disabled = false;
      submitBtn.textContent = 'Submit answers';
    }
  });

  function renderResult(data) {
    // Mark choices in the questions per the grading
    data.detailed.forEach(d => {
      const q = form.querySelector(`.training-question[data-qid="${d.question_id}"]`);
      if (!q) return;
      q.classList.add(d.correct ? 'correct' : 'incorrect');
      const choices = q.querySelectorAll('.training-choice');
      choices.forEach((c, idx) => {
        const isUser = idx === d.user_idx;
        const isCorrect = idx === d.correct_idx;
        if (isUser && d.correct) c.classList.add('correct');
        else if (isUser && !d.correct) c.classList.add('incorrect');
        if (!isUser && isCorrect && !d.correct) c.classList.add('correct-answer');
        // disable all
        const input = c.querySelector('input');
        if (input) input.disabled = true;
      });
      if (!d.correct && d.explanation) {
        const fb = q.querySelector('.training-question-feedback');
        if (fb) {
          // Use textContent (no innerHTML — security hook + xss safety)
          fb.textContent = 'Why: ' + d.explanation;
          fb.style.display = 'block';
        }
      }
    });

    // Hide the submit button
    form.querySelector('.training-quiz-actions').style.display = 'none';

    // Build result panel (no innerHTML — use DOM API)
    while (resultEl.firstChild) resultEl.removeChild(resultEl.firstChild);

    const scoreEl = document.createElement('div');
    scoreEl.className = 'training-quiz-result-score ' + (data.passed ? 'pass' : 'fail');
    scoreEl.textContent = data.score + ' / ' + data.total;
    resultEl.appendChild(scoreEl);

    const msg = document.createElement('div');
    msg.className = 'training-quiz-result-msg';
    if (data.passed) {
      msg.textContent = '✓ Passed (≥8 required). The next lesson is now unlocked.';
    } else {
      msg.textContent = '✗ Not passed. Review the explanations above, then try again.';
    }
    resultEl.appendChild(msg);

    const actionRow = document.createElement('div');
    if (data.passed) {
      const link = document.createElement('a');
      link.className = 'training-btn training-btn-primary';
      link.href = '/training/';
      link.textContent = '→ Back to path';
      actionRow.appendChild(link);
    } else {
      const retry = document.createElement('button');
      retry.className = 'training-btn training-btn-primary';
      retry.textContent = 'Retry quiz';
      retry.addEventListener('click', () => window.location.reload());
      actionRow.appendChild(retry);
    }
    resultEl.appendChild(actionRow);

    resultEl.style.display = 'block';
    resultEl.scrollIntoView({behavior: 'smooth'});
  }
})();
