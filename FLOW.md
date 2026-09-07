# iacsim — how it works, in one picture

**The question we answer:** *"If I change my infrastructure, how slow will my app feel — before I deploy?"*

```mermaid
flowchart TD
    files["📄 Your infrastructure files<br/>(Terraform / CloudFormation)"]
    read["READ THE FILES<br/>find every server, database, queue, function<br/>and where each one lives"]
    map["DRAW THE MAP<br/>boxes = things · arrows = who talks to whom<br/>every arrow carries the line of code that proves it"]
    price["PUT TIME ON EVERY ARROW<br/>how far apart the two boxes are<br/>+ how long the service takes to do its job"]
    walk["WALK ONE REQUEST ACROSS THE MAP<br/>steps one after another add up<br/>steps at the same time cost only the slowest"]
    explain["EXPLAIN THE RESULT<br/>total time · where it goes · what to change"]
    report["📋 REPORT<br/>terminal · PR comment · JSON · graph viewer"]
    numbers[("📊 THE NUMBERS<br/>public averages → your overrides → measured")]
    action[("🧭 ONE USER ACTION<br/>e.g. “user clicks Run”")]
    measure["📈 REAL MEASUREMENTS<br/>from your cloud account"]
    changed["📄 The same files after your change"]
    compare{"COMPARE<br/>before vs after"}
    ok["✅ within limit — merge"]
    block["⛔ slower than allowed — block the merge"]

    files -->|"open every file"| read
    read -->|"a list of things + where they are"| map
    map -->|"boxes and arrows"| price
    numbers -.->|"milliseconds per kind of hop"| price
    price -->|"every arrow now has a cost"| walk
    action -.->|"the path to follow"| walk
    walk -->|"total + cost of each hop"| explain
    explain -->|"ranked findings + suggestions"| report
    report -->|"result before"| compare
    changed -->|"run the same steps again → result after"| compare
    compare -->|"difference is small"| ok
    compare -->|"difference is too big"| block
    measure -.->|"replace the averages with your real numbers"| numbers
```

## The same thing in five lines

1. **Read** your infrastructure code and list everything in it.
2. **Map** it: boxes for things, arrows for who talks to whom, with proof for every arrow.
3. **Price** every arrow in milliseconds — distance plus the time the service takes.
4. **Walk** one real user action across the map and add it up.
5. **Explain** where the time goes, what to change, and what your next change would do.

## Three rules that make the numbers honest

- **Every arrow has evidence** — a sentence pointing at the line of code that proves it. A wrong guess is visible and can be corrected.
- **We compare, we don't prophesy** — "moving the database costs +299 ms" is reliable even when the base numbers are averages. The report always says which numbers it used.
- **Nothing is hard-wired** — a new cloud, file format or data source is a plugin you drop in. The engine never changes.

## One real example

Same app, two versions. The only difference: the database moved to another region.

| | page load |
|---|---|
| database in the same region | **57 ms** |
| database one region away | **356 ms** |
| the tool's verdict | *"+299 ms, 96 % is distance — bring the database back"* |

Found from the code alone, before anything was deployed.
