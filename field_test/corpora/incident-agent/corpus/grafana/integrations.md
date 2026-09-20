---
title: "Grafana OnCall integrations | Grafana OnCall documentation"
description: "An introduction to Grafana OnCall integrations."
---

> For a curated documentation index, see [llms.txt](/llms.txt). For the complete documentation index, see [llms-full.txt](/llms-full.txt).

# Grafana OnCall integrations

An integration serves as the primary entry point for alerts that are processed by Grafana OnCall. Integrations receive alerts through a unique API URL, interpret them using a set of templates tailored for the specific monitoring system, and initiate escalations as necessary.

For more information about how to configure an integration, refer to [Configure and manage integrations](/docs/oncall/latest/configure/integrations/integration-management/).

## Understand the integration alert flow

- An alert is received on an integration’s **Unique URL** as an HTTP POST request with a JSON payload (or via [Inbound email](/docs/oncall/latest/configure/integrations/references/inbound-email/) for email integrations).
- The incoming alert is routed according to the [Routing Template](/docs/oncall/latest/configure/jinja2-templating/#routing-template).
- Alerts are grouped based on the [Grouping ID Template](/docs/oncall/latest/configure/jinja2-templating/#grouping-id-template) and rendered using [Appearance Templates](/docs/oncall/latest/configure/jinja2-templating/#appearance-templates).
- The alert group can be published to messaging platforms, based on the **Publish to Chatops** configuration.
- The alert group is escalated to users according to the Escalation Chains selected for the route.
- An alert group can be acknowledged or resolved with status updates based on its [Behavioral Templates](/docs/oncall/latest/configure/jinja2-templating/#behavioral-templates).
- Users can perform actions listed in the [Alert Workflow](/docs/oncall/latest/set-up/get-started/#learn-about-the-alert-workflow) section.

## Explore available integrations

Refer to [Integration references](/docs/oncall/latest/configure/integrations/references/) for a list of available integrations and specific set up instructions.
