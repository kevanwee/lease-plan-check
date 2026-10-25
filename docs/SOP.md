# Standard operating procedure

1. Confirm scope: reconcile lease claims against supplied, calibrated premises geometry. Read the README's limitations first.
2. Run the documented sample command. The sample is synthetic and licensed for reuse.
3. Open the review HTML. Inspect classification, evidence and configuration before
   accepting any finding. An observed/calculated value is conditional on supplied inputs.
4. Inspect the annotated plan and calibration records. Correct controls, vertices, quoted lease readings or tolerances in matter.json and regenerate into a new directory. Update source hashes only after inspecting changed source documents. Assessment corrections in the review page remain separate from original measurements.
5. Assess every finding. Dismiss false positives with reasons; use correction overlays
   for your replacement assessment; use needs-information for unresolved evidence.
6. Identify the responsible reviewer and review basis. Download decisions. Real matters
   require the actual responsible person's decision; test sign-offs are synthetic only.
7. Finalize into a new directory. The CLI verifies the exact package and complete decision
   set. A mismatched fingerprint, changed source or missing decision is refused.
8. Verify the reviewed export. Retain the original analysis and decision history.
9. Hand off the usable output: `annotated-plan.png`, `measurements.json`, `geometry.local.json`, the source-linked reports and `review.html`. The plan is embedded in the offline review.
10. Confirm any external business outcome separately. Exporting a report makes no
    external change and grants no rights, compliance certification or survey authority.
