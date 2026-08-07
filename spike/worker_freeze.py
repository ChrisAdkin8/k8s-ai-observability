#!/usr/bin/env python3
"""Does the clock-offset freeze survive contact with the REAL worker() loop?

prompt-fault-injection.md priced W3 as "the softest number and the largest", on
exactly this reasoning: the clock-offset fix was "proven as a design on a scratch
harness, not inside worker() with a lock held and a profile poll running beside
it". spike/thaw_burst.py is that scratch harness. It models advance_to() and
nothing else, so it can only ever find arithmetic bugs in the offset itself.

This runs the shipped worker() in its own thread, against a real profile file on
disk, and drives the freeze in through the profile poll the way a drill would.
Everything it finds is a wiring bug the scratch harness structurally could not.

⚠️ REQUIRES W3's freeze knob and W0's faults block, which do not exist yet.
This is stage-3 evidence for prompt-fault-injection.md, kept because every
number that prompt now quotes comes from here and a reader who cannot rerun
it has to take them on trust. It ran green against the spike implementation
on 2026-08-07; the spike branch was deleted, as stage 3 says it should be.
Re-point it at the real implementation when W0 and W3 land.

Run from the repo root:  python3 spike/worker_freeze.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location("llmsim", os.path.join(ROOT, "scripts", "llm-sim.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


m = load()
POLL = 0.2          # the shipped default is 10s; the mechanism is the same
FREEZE_FOR = 3.0
FAILURES = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' -- ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)


def profile(**over):
    """A deliberately FAST profile, so wiring shows up in seconds of wall clock.

    ⚠️ The shipped shape takes ~5s to complete one request, and
    generation_tokens_total only moves on completion -- so a 2s observation
    window reads 0 -> 0 and every assertion over it passes vacuously. The first
    run of this script did exactly that and "proved" a freeze that was really
    just an idle counter. Service time here is ~0.116s against a 100 rps arrival,
    so ~100 requests complete per second and a 1s window is thousands of tokens.
    Nothing about the clock wiring depends on the rate; only the patience does.
    """
    p = dict(m.DEFAULT_PROFILE)
    p.update({"model_name": "sim-spike", "arrival_rate_rps": 100.0, "seed": 4242,
              "prompt_tokens": {"mean": 64, "stddev": 0},
              "generation_tokens": {"mean": 32, "stddev": 0},
              "base_ttft_seconds": 0.02, "base_itl_seconds": 0.002})
    p.update(over)
    return p


class Rig:
    """A real worker() thread over a real profile file, as production runs it."""

    def __init__(self, prof, poll=POLL, worker_fn=None):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.write(prof)
        sim = m.Simulator(m.validate_profile(prof), start_time=time.monotonic())
        self.state = m.State(sim, self.path)
        self.thread = threading.Thread(
            target=worker_fn or m.worker, args=(self.state,), kwargs={"poll_seconds": poll},
            daemon=True)

    def write(self, prof):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(prof, fh)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.state.stop.set()
        self.thread.join(timeout=5)
        os.unlink(self.path)

    def sample(self):
        with self.state.lock:
            s = self.state.sim
            return {"gen": s.generation_tokens_total, "running": len(s.running),
                    "now": s.now, "gencount": s.profile_generation}


# ⚠️ POLL FOR THE TRANSITION, NEVER SLEEP THROUGH IT (iron rule 5). Every wait below was
# `time.sleep(POLL * n)` — single-shot against a racing producer. `profile_generation`
# is bumped by the worker when it applies a profile, so it is the observable that says
# the write LANDED rather than merely that time passed. A fixed sleep under scheduler
# delay or slow file I/O samples before the freeze arrives; the check then fails for a
# reason that has nothing to do with the mechanism, which is the worst kind of flake
# because it is blamed on the thing being measured.
def wait_for_generation(rig, at_least, deadline_s=15.0):
    """Block until the worker has applied a profile newer than `at_least`."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if rig.sample()["gencount"] >= at_least:
            return True
        time.sleep(0.05)
    return False


def q1_freeze_holds_and_thaws():
    """The whole mode: counters stop, the engine still claims work, and it lifts."""
    print("\nQ1  freeze holds the counters, and the thaw is deliverable")
    prof = profile()
    with Rig(prof) as rig:
        time.sleep(2.0)
        before = rig.sample()
        rig.write(profile(faults={"freeze": True}))
        landed = wait_for_generation(rig, before["gencount"] + 1)
        check(landed, "the freeze profile was applied (polled, not slept through)",
              "profile_generation advanced")
        frozen_at = rig.sample()
        time.sleep(FREEZE_FOR)
        still = rig.sample()

        check(still["gen"] == frozen_at["gen"],
              "generation_tokens_total does not advance while frozen",
              f"{frozen_at['gen']} -> {still['gen']}")
        check(still["running"] > 0 and still["running"] == frozen_at["running"],
              "num_requests_running holds its pre-freeze value",
              f"{frozen_at['running']} requests held")
        check(before["gen"] < frozen_at["gen"],
              "counters WERE advancing before the freeze (the control)",
              f"{before['gen']} -> {frozen_at['gen']}")

        rig.write(profile(faults={"freeze": False}))
        thaw_landed = wait_for_generation(rig, still["gencount"] + 1)
        check(thaw_landed, "the thaw profile was applied (polled, not slept through)",
              "profile_generation advanced again")
        time.sleep(POLL * 2)          # let the resumed clock actually produce tokens
        thawed = rig.sample()
        check(thawed["gen"] > still["gen"],
              "the thaw is delivered through the poll and counters resume",
              f"{still['gen']} -> {thawed['gen']}")


def q1b_thaw_burst(gap=0.3):
    """The burst, measured as a scrape gap -- which is the unit that matters.

    A "burst" is only meaningful against a scrape interval: Prometheus sees the
    counter delta between two scrapes, so the question is how many tokens land in
    the FIRST gap after the thaw versus a gap during normal running.
    """
    print(f"\nQ1b the thaw burst, in tokens per {gap}s scrape gap")

    def one(skip_advance):
        """skip_advance=True is the NAIVE freeze: stop calling advance_to()."""
        def w(state, poll_seconds=10.0):
            frozen_seconds = 0.0
            last_tick = time.monotonic()
            while not state.stop.is_set():
                wall = time.monotonic()
                with state.lock:
                    frozen = bool(state.sim.profile.get("faults", {}).get("freeze"))
                    if frozen and skip_advance:
                        due = wall + 0.05                 # naive: don't advance at all
                    else:
                        if frozen:
                            frozen_seconds += wall - last_tick
                        state.sim.advance_to(wall - frozen_seconds)
                        due = state.sim.next_event_time()
                    last_tick = wall
                state.stop.wait(max(0.01, min(due - (time.monotonic() - frozen_seconds), 0.5)))
        with Rig(profile(), worker_fn=w) as rig:
            time.sleep(1.0)
            a = rig.sample()["gen"]
            time.sleep(gap)
            b = rig.sample()["gen"]
            normal = b - a                                # a normal scrape gap
            with rig.state.lock:
                rig.state.sim.apply_profile(
                    m.validate_profile(profile(faults={"freeze": True})))
            time.sleep(FREEZE_FOR)
            with rig.state.lock:
                rig.state.sim.apply_profile(
                    m.validate_profile(profile(faults={"freeze": False})))
            c = rig.sample()["gen"]
            time.sleep(gap)
            d = rig.sample()["gen"]
            return normal, d - c

    naive_normal, naive_first = one(True)
    fixed_normal, fixed_first = one(False)
    print(f"        naive freeze : {naive_normal:6d} normal gap -> {naive_first:6d} "
          f"first post-thaw gap  ({naive_first / max(1, naive_normal):.1f}x)")
    print(f"        clock offset : {fixed_normal:6d} normal gap -> {fixed_first:6d} "
          f"first post-thaw gap  ({fixed_first / max(1, fixed_normal):.1f}x)")
    check(naive_first > naive_normal * 3,
          "NAIVE freeze replays the frozen interval into one scrape gap",
          f"{naive_first / max(1, naive_normal):.1f}x a normal gap")
    check(fixed_first <= fixed_normal * 1.5,
          "clock offset: the first post-thaw gap is an ordinary gap",
          f"{fixed_first / max(1, fixed_normal):.1f}x a normal gap")


def q2_poll_cadence_on_sim_clock_wedges():
    """Keying the poll off the simulated clock makes the freeze unliftable."""
    print("\nQ2  what the NAIVE poll cadence does (rule 18: break it first)")

    def naive_worker(state, poll_seconds=10.0):
        """worker() as shipped, but the poll cadence keyed off `now` (sim time)."""
        last_poll, frozen_seconds = 0.0, 0.0
        last_tick = time.monotonic()
        while not state.stop.is_set():
            wall = time.monotonic()
            with state.lock:
                if state.sim.profile.get("faults", {}).get("freeze"):
                    frozen_seconds += wall - last_tick
                last_tick = wall
                now = wall - frozen_seconds
                state.sim.advance_to(now)
                due = state.sim.next_event_time()
            if state.profile_path and now - last_poll >= poll_seconds:   # <-- the bug
                last_poll = now
                try:
                    new = m.read_profile_file(state.profile_path)
                except Exception:                                 # noqa: BLE001
                    pass
                else:
                    with state.lock:
                        if new != state.sim.profile:
                            state.sim.apply_profile(new)
            state.stop.wait(max(0.01, min(due - (time.monotonic() - frozen_seconds), 0.5)))

    def freeze_then_thaw(worker_fn):
        """(frozen gen, gen after the thaw window, did the thaw ever land).

        ⚠️ THE THIRD RETURN IS THE WHOLE POINT IN THE NAIVE ARM. Both waits used to be
        fixed sleeps, which cannot distinguish "the thaw arrived and did nothing" from
        "the thaw never arrived" — and in the naive worker it never arrives, because the
        profile poll keys off the frozen simulated clock. Polling to a deadline and
        reporting that it expired turns a silence into an observation.
        """
        with Rig(profile(), worker_fn=worker_fn) as rig:
            time.sleep(1.0)
            base = rig.sample()["gencount"]
            rig.write(profile(faults={"freeze": True}))
            froze = wait_for_generation(rig, base + 1)
            frozen = rig.sample()
            rig.write(profile(faults={"freeze": False}))
            thawed_ok = wait_for_generation(rig, frozen["gencount"] + 1, deadline_s=8.0)
            after = rig.sample()
            return frozen["gen"], after["gen"], (froze, thawed_ok)

    # ⚠️ Both arms, or this proves nothing. The first version of this check ran
    # the naive arm alone and asserted "counters did not move" -- which was true,
    # and would have stayed true if the freeze had never engaged at all. It
    # passed against a counter that was 0 for an unrelated reason.
    n_before, n_after, (n_froze, n_thawed) = freeze_then_thaw(naive_worker)
    f_before, f_after, (f_froze, f_thawed) = freeze_then_thaw(None)

    check(f_froze and f_thawed,
          "CONTROL: both profile writes were applied (polled to a deadline)",
          "freeze landed, thaw landed")
    check(f_after > f_before,
          "CONTROL: the shipped worker() sees the thaw and counters resume",
          f"{f_before} -> {f_after}")

    # ⚠️ THE TWO NAIVE ASSERTIONS SAY DIFFERENT THINGS, AND BOTH ARE NEEDED. That the
    # counters did not move is consistent with the freeze never engaging at all. That
    # the freeze DID land and the thaw provably did not — polled to an 8s deadline
    # rather than slept through — is what identifies the bug as the poll cadence.
    check(n_froze and not n_thawed,
          "NAIVE cadence: the freeze landed, and the thaw NEVER did",
          "polled to the deadline; profile_generation never advanced again")
    check(n_after == n_before,
          "NAIVE cadence: counters are still where the freeze left them",
          f"still at {n_after}")
    print("        ^ the failure the fix prevents. Nothing errors, the pod stays")
    print("          Ready, and /metrics serves the last state forever.")


def q3_sleep_against_wall_clock_spins():
    """Comparing a simulated `due` to time.monotonic() busy-spins after a freeze."""
    print("\nQ3  what the NAIVE sleep does (rule 18: break it first)")
    passes = {"n": 0}

    def make_worker(sleep_against_wall):
        def w(state, poll_seconds=10.0):
            frozen_seconds = 0.0
            last_tick = time.monotonic()
            while not state.stop.is_set():
                wall = time.monotonic()
                with state.lock:
                    if state.sim.profile.get("faults", {}).get("freeze"):
                        frozen_seconds += wall - last_tick
                    last_tick = wall
                    state.sim.advance_to(wall - frozen_seconds)
                    due = state.sim.next_event_time()
                passes["n"] += 1
                if sleep_against_wall:                       # <-- the bug
                    state.stop.wait(max(0.01, min(due - time.monotonic(), 0.5)))
                else:                                        # <-- the fix
                    state.stop.wait(
                        max(0.01, min(due - (time.monotonic() - frozen_seconds), 0.5)))
        return w

    # ⚠️ THE SHIPPED ARRIVAL RATE, NOT THIS SCRIPT'S FAST ONE, and that is the
    # whole point of the check. The spin is the gap between "sleep until the next
    # event" and "sleep the 0.01s floor". At 100 rps the next event is ~0.01s
    # away anyway, so both arms sleep the floor and the bug is invisible -- which
    # is exactly what this check reported on its first run. At the shipped 1.8 rps
    # events are ~0.5s apart and the difference is two orders of magnitude.
    def slow(**over):
        p = dict(m.DEFAULT_PROFILE)
        p.update({"model_name": "sim-spike-slow", "arrival_rate_rps": 1.8, "seed": 4242})
        p.update(over)
        return p

    def count_passes(sleep_against_wall):
        # ⚠️ The freeze is injected DIRECTLY under the lock, not through the
        # profile file: these variants carry no poll, so writing the file would
        # leave the freeze unengaged and the loop would look healthy for the
        # wrong reason. That is what the first draft of this check did.
        passes["n"] = 0
        with Rig(slow(), worker_fn=make_worker(sleep_against_wall)) as rig:
            time.sleep(0.5)
            with rig.state.lock:
                rig.state.sim.apply_profile(
                    m.validate_profile(slow(faults={"freeze": True})))
            t0 = time.monotonic()
            time.sleep(FREEZE_FOR)
            return passes["n"], time.monotonic() - t0

    naive, naive_s = count_passes(True)
    fixed, fixed_s = count_passes(False)
    print(f"        wall-clock comparison: {naive / naive_s:6.0f} passes/s naive")
    print(f"                               {fixed / fixed_s:6.0f} passes/s fixed")
    check(naive / naive_s > 20 * max(1.0, fixed / fixed_s),
          "NAIVE sleep: the loop collapses to the 0.01s floor and busy-spins",
          f"{naive / naive_s:.0f}/s vs {fixed / fixed_s:.0f}/s -- a core burned "
          f"for the life of the pod, with correct output")


def q4_default_render_byte_identical():
    """W0.5: adding the schema must not move the default exposition by one byte."""
    print("\nQ4  the default surface is untouched (W0.5 / rule 1)")
    p_none = profile()
    p_none.pop("faults", None)
    a = m.Simulator(m.validate_profile(p_none), start_time=0.0)
    b = m.Simulator(m.validate_profile(profile(faults={"freeze": False})), start_time=0.0)
    a.advance_to(120.0)
    b.advance_to(120.0)
    ra, rb = a.render(), b.render()
    check(ra == rb, "no `faults` key and an inert `faults` block render identically",
          f"{len(ra)} bytes")
    check("llmsim_fault_active" not in ra,
          "llmsim_fault_active is absent from the default surface")

    c = m.Simulator(m.validate_profile(profile(faults={"freeze": True})), start_time=0.0)
    c.advance_to(120.0)
    check("llmsim_fault_active" in c.render(),
          "llmsim_fault_active IS emitted while a fault is held (the control)")


def q5_typo_is_rejected():
    """A fault surface that ignores a typo grades your spelling, not the alert."""
    print("\nQ5  a misspelled fault is refused rather than silently ignored")
    for bad, why in [({"freze": True}, "misspelled key"),
                     ({"freeze": "yes"}, "wrong type"),
                     ("freeze", "not an object")]:
        try:
            m.validate_profile(profile(faults=bad))
        except m.ProfileError as exc:
            check(True, f"rejected: {why}", str(exc)[:72])
        else:
            check(False, f"rejected: {why}", "ACCEPTED SILENTLY")
    try:
        m.validate_profile(profile(faults={"freeze": True, "_note": "why this ran"}))
        check(True, "_note travels with a fault document and is ignored")
    except m.ProfileError as exc:
        check(False, "_note travels with a fault document", str(exc))


def q6_render_is_still_a_pure_read():
    """Freezing must not disturb the property --selftest already asserts."""
    print("\nQ6  render() stays a pure read while frozen (llm-sim.py observations)")
    sim = m.Simulator(m.validate_profile(profile(faults={"freeze": True})), start_time=0.0)
    sim.advance_to(120.0)
    before = sim.observations
    sim.render()
    sim.render()
    check(sim.observations == before,
          "two renders while frozen observe nothing",
          f"observations {before}")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    q4_default_render_byte_identical()
    q5_typo_is_rejected()
    q6_render_is_still_a_pure_read()
    q1_freeze_holds_and_thaws()
    q1b_thaw_burst()
    q2_poll_cadence_on_sim_clock_wedges()
    q3_sleep_against_wall_clock_spins()
    print(f"\n{'FAILURES: ' + ', '.join(FAILURES) if FAILURES else 'all questions answered'}")
    sys.exit(1 if FAILURES else 0)
