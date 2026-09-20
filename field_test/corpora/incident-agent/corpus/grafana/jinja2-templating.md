---
title: "Configure templates for OnCall OSS | Grafana OnCall documentation"
description: "Understand how to configure and customize alert templates for Grafana OnCall OSS."
---

> For a curated documentation index, see [llms.txt](/llms.txt). For the complete documentation index, see [llms-full.txt](/llms-full.txt).

# Configure templates for OnCall OSS

Grafana OnCall OSS integrates with your monitoring systems using webhooks with JSON payloads. By default, these webhooks deliver raw JSON payloads. Grafana OnCall OSS applies a pre-configured alert template to modify these payloads into a more human-readable format. These templates are customizable, enabling you to format alerts and notify different escalation chains based on alert content.

## Understand your alert payload

Alerts received by Grafana OnCall contain metadata as key-value pairs in a JSON object.

All alerts and alert groups in Grafana OnCall contain the following fields.

- `Title`
- `Message`
- `Image Url`
- `Grouping Id`
- `Resolved by source`
- `Acknowledged by source`
- `Source link`

The following is an example of an alert initiated by Grafana Alerting and received by Grafana OnCall:

JSON ![Copy code to clipboard](/media/images/icons/icon-copy-small-2.svg) Copy

```json
{
  "dashboardId": 1,
  "title": "[Alerting] Panel Title alert",
  "message": "Notification Message",
  "evalMatches": [
    {
      "value": 1,
      "metric": "Count",
      "tags": {}
    }
  ],
  "imageUrl": "https://grafana.com/static/assets/img/blog/mixed_styles.png",
  "orgId": 1,
  "panelId": 2,
  "ruleId": 1,
  "ruleName": "Panel Title alert",
  "ruleUrl": "http://localhost:3000/d/hZ7BuVbWz/test-dashboard?fullscreen\u0026edit\u0026tab=alert\u0026panelId=2\u0026orgId=1",
  "state": "alerting",
  "tags": {
    "tag name": "tag value"
  }
}
```

### Map payloads to OnCall fields

Each field of an alert in OnCall is mapped to the JSON payload keys.

Grafana OnCall converts the JSON payload to specific alert fields. For example:

- `{{ payload.title }}` -&gt; `Title`
- `{{ payload.message }}` -&gt; `Message`
- `{{ payload.imageUrl }}` -&gt; `Image Url`

Behavioral mappings include:

- `{{ payload.ruleId }}` -&gt; `Grouping Id`
- `{{ 1 if payload.state == 'OK' else 0 }}` -&gt; `Resolve Signal`

## Types of templates

Alert templates allow you to format any alert fields recognized by Grafana OnCall. You can customize default alert templates for all the different notification methods.

> Note
> 
> For conditional templates, the output should be `True` to be applied, for example `{{ True if payload.state == 'OK' else False }}`

### Routing template

Routing templates determine how alerts are routed to different escalation chains based on alert content.

These are conditional templates, output should be `True`.

### Appearance templates

Appearance templates customize how alerts are displayed across various platforms, including the web, Slack, MS Teams, SMS, phone calls, emails, and mobile app push notifications.

You can use appearance templates to define `Title`, `Message`, and `Image URL` depending on the notification method.

- `Title`, `Message`, `Image URL` for Web
- `Title`, `Message`, `Image URL` for Slack
- `Title`, `Message`, `Image URL` for MS Teams
- `Title`, `Message`, `Image URL` for Telegram
- `Title` for SMS
- `Title` for Phone Call
- `Title`, `Message` for Email
- `Title`, `Message` for push notifications

### Behavioral templates

Behavioral templates control alert behaviors such as grouping, auto-resolution, and acknowledgment.

- `Grouping Id`: Applied to every incoming alert payload after routing. Determines how alerts are grouped.
- `Autoresolution`: Automatically resolves alert groups with a status of `Resolved by source` (conditional template).
- `Auto acknowledge`: Automatically acknowledges alert groups with a status of `Acknowledged by source` (conditional template).
- `Source link`: Customizes the URL link provided as the alert’s source.

> Tip
> 
> As a best practice, add Playbooks, useful links, or checklists to the alert message.

### Integration templates

Integration templates are applied to alerts that originated from a specific integration to define alert rendering and behavior.

Grafana OnCall provides pre-configured default Jinja templates for supported integrations. For any monitoring system not available in the Grafana OnCall integrations list, configure a [Webhook integration](/docs/oncall/latest/configure/integrations/references/webhook/) and configure your templates as needed.

## Edit templates

1. Open the **Integration** page for the desired integration.
2. Navigate to the **Templates** section and click **Edit** to see previews of all templates for the integration.
3. Select the template to edit and click **Edit**. The template editor will open with three columns: example alert payload, the template itself, and the rendered result.
4. Choose a **Recent Alert group** to see its latest alert payload. Click **Edit** to modify this payload.
5. Alternatively, click **Use custom payload** to write your own payload and see its rendering.
6. Press `Control + Enter` in the editor to view suggestions.
7. Click **Cheatsheet** in the second column for inspiration.
8. For messenger templates, click **Save and open Alert Group in ChatOps** to see how the alert renders in the messenger. Note: The alert group must exist in the messenger to preview the template.
9. Click **Save** to save the template.
