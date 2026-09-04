# Problem Statement

Build a system that converts Infrastructure-as-Code (IaC) files (e.g., Terraform) into a latency simulation model that predicts how an application's infrastructure impacts end-to-end latency.

The goal is to analyze infrastructure topology, dependencies, and networking configuration from Terraform and simulate latency across services, enabling engineers to evaluate performance before deploying changes.

## Key Requirements

- Parse Terraform infrastructure files.
- Convert infrastructure resources into a simulation graph.
- Model network latency between services, databases, load balancers, and regions.
- Simulate end-to-end request latency through the infrastructure.
- Identify which infrastructure components contribute most to latency.
- Allow engineers to test infrastructure changes and compare their latency impact.

## Constraints

- Infrastructure design is a major variable affecting latency.
- The problem is inherently complex because latency depends on multiple interacting infrastructure components and network paths.
- The solution should produce a practical plan or prototype that can be reviewed and iterated upon.

---

# Explanation

## The situation

When you build an app on the cloud, you describe your infrastructure in code — Terraform files that say "I want 3 servers in Mumbai, a database in Singapore, a load balancer in front, connected like this." That's Infrastructure-as-Code (IaC).

The problem is: those files tell you *what* you're building, but not *how fast it will feel* to a user. A request from a user might go: user → load balancer → web server → API service → database → back. Every hop adds a few milliseconds. If your database sits in a different region than your API server, every single request pays a cross-region round-trip. You usually only discover this *after* deploying, when someone complains the app is slow.

## What we're asked to build

A tool that reads Terraform files and answers the question **"how slow will this be, and why?"** — before anything is deployed. It works in roughly five steps:

1. **Read the Terraform** — understand what resources exist (servers, databases, load balancers, VPCs, regions).
2. **Turn it into a graph** — a map of boxes and arrows: which component talks to which.
3. **Put latency numbers on the arrows** — e.g. same availability zone ≈ 0.5 ms, cross-region ≈ 70 ms, going through a load balancer adds ≈ 2 ms, database query ≈ 5 ms.
4. **Simulate a request** — trace a user request through the graph and add up the delays to get a total end-to-end latency.
5. **Show the bottlenecks** — tell the engineer "80% of your latency comes from the API → DB cross-region hop."

Then the bonus: let an engineer change the Terraform ("what if I move the DB to the same region?"), re-run, and **compare** before vs. after.

## Two layers of latency — and which one we're building first

A request's time splits into **infrastructure latency** (how far the hops are, what each AWS service costs, how many hops and whether they run in parallel) and **application latency** (what the code does inside a Lambda — logic, third-party API calls). We're building around the infrastructure layer first, because that's what Terraform describes. The numbers start as fixed public defaults, can be overridden by the team, and later get replaced by live measurements from CloudWatch — same file format, nothing else changes. Measured Lambda durations include the code time, so the application layer folds in automatically once we calibrate.

## Why it's hard

Latency isn't one number you look up — it emerges from many pieces interacting: which region things are in, what's between them (NAT gateways, load balancers, VPC peering), which services call which, and in what order (sequential calls stack up; parallel ones don't). The tool has to model all of that reasonably, not perfectly.

## What "done" looks like

Not a production-grade product — a **working prototype or a solid plan** that someone can review, poke at, and improve. Think: feed it a sample Terraform project, get back a latency estimate plus a ranked list of what's slowing things down.

**One-line version:** *"Turn Terraform into a map, simulate a request walking across that map, and tell engineers where the time goes — before they deploy."*
