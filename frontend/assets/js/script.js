/* ============================================================
   K WAY AgentPortal — Global Script
   Architecture: Apple progressive-enhancement pattern
   ============================================================ */

(function () {
  'use strict';

  /* ── 1. Scroll Reveal (Apple IntersectionObserver pattern) ── */
  const revealObserver = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          revealObserver.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.15 }
  );
  document.querySelectorAll('.reveal').forEach((el) => revealObserver.observe(el));

  /* ── 2. Navigation scroll effect (frosted glass) ─────────── */
  const globalHeader = document.querySelector('.ac-globalheader');
  if (globalHeader) {
    const onScroll = () => {
      globalHeader.classList.toggle('is-scrolled', window.scrollY > 44);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  /* ── 3. Smooth anchor scrolling (Apple nav link pattern) ──── */
  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener('click', (e) => {
      const target = document.querySelector(anchor.getAttribute('href'));
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth' });
      }
    });
  });
})();
