# Legacy policies: do not apply alongside the current chart

The ERP chart owns all current NetworkPolicies, including default deny,
Ingress-controller access, API-to-Core and one DATA IP/port per egress rule.
The old default-deny.yaml and allow-erp.yaml are kept as historical examples
for review and are not part of bootstrap, Argo sync or the separate-repo export.

Allow policies are additive. Applying the legacy allow-erp policy would reopen
the old IP/port cross-product and cannot be corrected by adding a default-deny.
Before migrating an existing namespace, list and review all current policies;
remove superseded legacy policies only after the new chart policies are present
and permitted/denied flows have been tested. No live deletion is automated here.
