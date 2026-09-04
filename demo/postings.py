"""The fictional postings the demo runs on.

Every company here is one of Microsoft's standard placeholder names, and every
description was written for this file. Nothing was copied from a real posting,
and nothing is fetched from LinkedIn when the demo runs.

`days_ago` rather than a fixed date: freshness is 10% of the score and decays
over 30 days, so hardcoded dates would make the demo look progressively staler
and stop matching what a new visitor sees. run.py turns these into real dates
at launch.

The set is chosen to exercise the pipeline rather than to flatter it. It
includes the postings that get thrown out, because the rejected list at the
bottom of the jobs page is the thing that stops a mistuned filter quietly
eating everything.

`llm=None` means the model has never read that posting, so it is scored on
regex alone — the degraded path the README describes.
"""

POSTINGS = [
    dict(
        days_ago=1, company="Contoso", title="Junior .NET Developer (m/f/d)",
        location="Lisbon, Portugal", seniority="Entry level",
        employment_type="Full-time",
        html="""<p>Contoso is looking for a junior developer to join the team behind
        our claims platform. You will work alongside three other engineers on a
        service that processes several million records a day.</p>
        <p><strong>What you will do</strong></p><ul>
        <li>Build and maintain REST APIs in C# and ASP.NET Core.</li>
        <li>Write the tests that gate our deploy pipeline.</li>
        <li>Work with PostgreSQL, including the schema and the queries on it.</li>
        <li>Take part in code review and in our weekly design discussions.</li>
        </ul><p><strong>What we are looking for</strong></p><ul>
        <li>Around a year of professional experience, or a strong internship.</li>
        <li>Comfortable in C# or another statically typed language.</li>
        <li>Some exposure to CI/CD; we use GitHub Actions.</li>
        <li>English. Portuguese is useful but not required.</li>
        </ul><p>We work hybrid, two days a week in the Lisbon office.</p>""",
        llm=dict(
            work_mode="hybrid", work_mode_evidence="two days a week in the Lisbon office",
            remote_scope="unclear", remote_countries=[], onsite_days_per_week=2,
            years_required=1, years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["English"], contract_type="permanent", salary=None,
            summary="Build and maintain C# REST APIs on a claims platform backed by PostgreSQL."),
    ),
    dict(
        days_ago=2, company="Fabrikam", title="Graduate Backend Engineer",
        location="Remote, Portugal", seniority="Entry level",
        employment_type="Full-time",
        html="""<p>Fabrikam builds logistics software. We are hiring graduates onto
        the platform team, fully remote within Portugal.</p>
        <p><strong>The role</strong></p><ul>
        <li>Work on our internal APIs, written in TypeScript and Node.js.</li>
        <li>Own a service end to end, with support, once you are settled.</li>
        <li>Help move the remaining scheduled jobs onto our new runner.</li>
        </ul><p><strong>You might fit if</strong></p><ul>
        <li>You have written and shipped something, professionally or not.</li>
        <li>You know your way around SQL and relational modelling.</li>
        <li>You would rather ask than guess.</li>
        </ul><p>No prior commercial experience required. Salary 28.000 - 34.000 EUR.</p>""",
        llm=dict(
            work_mode="remote", work_mode_evidence="fully remote within Portugal",
            remote_scope="listed", remote_countries=["Portugal"],
            onsite_days_per_week=None, years_required=None,
            years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["English"], contract_type="permanent",
            salary="28.000 - 34.000 EUR",
            summary="Graduate role on a logistics platform team writing TypeScript and Node.js services."),
    ),
    dict(
        days_ago=3, company="Litware", title="Junior Fullstack Developer",
        location="Lisbon, Portugal", seniority="Entry level",
        employment_type="Full-time",
        html="""<p>Join Litware's product team building the dashboard our customers
        use every day.</p><p><strong>Stack</strong></p><ul>
        <li>React and TypeScript on the front end.</li>
        <li>ASP.NET Core and Entity Framework Core behind it.</li>
        <li>Azure, with infrastructure described in Terraform.</li>
        </ul><p><strong>Requirements</strong></p><ul>
        <li>1-2 years with a modern web framework.</li>
        <li>Willingness to work across the stack rather than pick a side.</li>
        <li>Fluent English; the team is distributed across four countries.</li>
        </ul><p>Hybrid, one day a week in our Lisbon office.</p>""",
        llm=dict(
            work_mode="hybrid", work_mode_evidence="one day a week in our Lisbon office",
            remote_scope="unclear", remote_countries=[], onsite_days_per_week=1,
            years_required=1, years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["English"], contract_type="permanent", salary=None,
            summary="Fullstack work on a customer dashboard in React, TypeScript and ASP.NET Core."),
    ),
    dict(
        days_ago=5, company="Trey Research", title="Software Engineer I",
        location="Remote, European Union", seniority="Entry level",
        employment_type="Full-time",
        html="""<p>Trey Research is a distributed company. This role is fully remote
        and open to candidates anywhere in the EU.</p>
        <p>You will join the data platform group, working on the services that move
        and validate our customers' research datasets.</p><ul>
        <li>Python and Go, with PostgreSQL underneath.</li>
        <li>Everything runs in containers on Kubernetes.</li>
        <li>We deploy several times a day and expect you to, too.</li>
        </ul><p>We are happy to hire someone early in their career who is curious
        and writes clearly.</p>""",
        llm=dict(
            work_mode="remote",
            work_mode_evidence="fully remote and open to candidates anywhere in the EU",
            remote_scope="eu_eea", remote_countries=[], onsite_days_per_week=None,
            years_required=None, years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["English"], contract_type="permanent", salary=None,
            summary="Data platform engineering on containerised Python and Go services."),
    ),
    dict(
        days_ago=4, company="Adventure Works", title="Junior Angular Developer",
        location="Oeiras, Portugal", seniority="Entry level",
        employment_type="Full-time",
        html="""<p>We are rebuilding our booking front end and need a junior
        developer to work on it with two senior engineers.</p><ul>
        <li>Angular, TypeScript, RxJS.</li>
        <li>A .NET API you will occasionally need to change.</li>
        <li>Unit tests are not optional here.</li>
        </ul><p>Three days a week in Oeiras, two from home.</p>
        <p>Portuguese and English both needed; our support team works in
        Portuguese.</p>""",
        llm=dict(
            work_mode="hybrid", work_mode_evidence="Three days a week in Oeiras",
            remote_scope="unclear", remote_countries=[], onsite_days_per_week=3,
            years_required=1, years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["Portuguese", "English"], contract_type="permanent",
            salary=None,
            summary="Front-end work rebuilding a booking flow in Angular with a .NET API behind it."),
    ),
    dict(
        days_ago=8, company="Tailspin Toys", title="Junior Developer - Internal Tools",
        location="Lisbon, Portugal", seniority="Entry level",
        employment_type="Full-time",
        html="""<p>Small team, broad remit. You would be the third engineer on our
        internal tools group, which builds whatever the rest of the company needs
        and cannot buy.</p><ul>
        <li>Mostly C# and a bit of Python.</li>
        <li>SQL Server, moving to PostgreSQL over the next year.</li>
        <li>You will talk to the people who use what you build, daily.</li>
        </ul><p>On-site in Lisbon. We think juniors learn faster in a room.</p>""",
        llm=dict(
            work_mode="onsite", work_mode_evidence="On-site in Lisbon",
            remote_scope="unclear", remote_countries=[], onsite_days_per_week=5,
            years_required=None, years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["English"], contract_type="permanent", salary=None,
            summary="Internal tooling in C# and Python for a small team, on-site in Lisbon."),
    ),
    dict(
        days_ago=11, company="Lucerne Publishing", title="Junior Web Developer",
        location="Lisbon Metropolitan Area", seniority="Entry level",
        employment_type="Full-time",
        html="""<p>Lucerne Publishing is hiring a junior web developer for our
        digital subscriptions team.</p><ul>
        <li>JavaScript, some TypeScript, React.</li>
        <li>A REST API you will consume more often than change.</li>
        <li>Occasional CSS work; we have a design system, so no surprises.</li>
        </ul><p>Entry level. We will train you on the rest.</p>""",
        # Never read by the model: scored on regex alone, and the location has
        # no country in it — the case locate() is careful about.
        llm=None,
    ),
    dict(
        days_ago=6, company="Woodgrove Bank", title="Junior Software Developer",
        location="Lisbon, Portugal", seniority="Mid-Senior level",
        employment_type="Full-time",
        html="""<p>Woodgrove Bank is recruiting for our payments engineering
        department.</p><ul>
        <li>Java and Spring Boot, with some C# on the legacy side.</li>
        <li>Oracle, moving slowly to PostgreSQL.</li>
        <li>Regulated environment; expect process and paperwork.</li>
        </ul><p>Hybrid, three days on site. 3+ years preferred but we will consider
        strong candidates with less.</p>""",
        # Titled junior, labelled Mid-Senior by LinkedIn, and the model judged it
        # not junior-suitable. The signed seniority component is what stops this
        # outranking a genuine junior role on stack keywords alone.
        llm=dict(
            work_mode="hybrid", work_mode_evidence="Hybrid, three days on site",
            remote_scope="unclear", remote_countries=[], onsite_days_per_week=3,
            years_required=3, years_are_hard_requirement=False, junior_suitable=False,
            languages_required=["English"], contract_type="permanent", salary=None,
            summary="Payments engineering in Java and Spring Boot inside a regulated bank."),
    ),
    dict(
        days_ago=25, company="Coho Vineyard", title="Trainee Developer",
        location="Remote, Portugal", seniority="Internship",
        employment_type="Internship",
        # Old enough that freshness has decayed most of the way.
        html="""<p>Twelve-month traineeship, fully remote, aimed at people moving
        into software from another field.</p><ul>
        <li>Structured curriculum for the first three months.</li>
        <li>Python, SQL, and a real project from month four.</li>
        <li>A mentor, and a genuine chance of a permanent offer at the end.</li>
        </ul><p>No experience required.</p>""",
        llm=dict(
            work_mode="remote", work_mode_evidence="fully remote",
            remote_scope="listed", remote_countries=["Portugal"],
            onsite_days_per_week=None, years_required=0,
            years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["English"], contract_type="internship",
            salary="1.100 EUR/month",
            summary="Twelve-month remote traineeship teaching Python and SQL with a mentor."),
    ),
    dict(
        days_ago=9, company="Proseware", title="Junior QA Automation Engineer",
        location="Cascais, Portugal", seniority="Entry level",
        employment_type="Full-time",
        html="""<p>Help us stop shipping regressions. You will own our end to end
        suite and the pipeline that runs it.</p><ul>
        <li>TypeScript and Playwright.</li>
        <li>GitHub Actions, and the reporting on top of it.</li>
        <li>You will write some production code too; this is not a silo.</li>
        </ul><p>Two days a week in Cascais.</p>""",
        llm=dict(
            work_mode="hybrid", work_mode_evidence="Two days a week in Cascais",
            remote_scope="unclear", remote_countries=[], onsite_days_per_week=2,
            years_required=1, years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["English"], contract_type="permanent", salary=None,
            summary="Own an end-to-end Playwright suite and the CI pipeline that runs it."),
    ),

    # ----------------------------------------------------- these get thrown out
    # One posting per hard filter, so the rejected list on the jobs page shows
    # each rule actually firing rather than a single representative case.
    dict(
        days_ago=2, company="Northwind Traders", title="Senior Backend Engineer",
        location="Lisbon, Portugal", seniority="Mid-Senior level",
        employment_type="Full-time",
        html="""<p>We are looking for a senior engineer to lead the redesign of our
        order pipeline.</p><p>8+ years of professional experience with distributed
        systems required.</p>""",
        llm=dict(
            work_mode="hybrid", work_mode_evidence="two days in the office",
            remote_scope="unclear", remote_countries=[], onsite_days_per_week=2,
            years_required=8, years_are_hard_requirement=True, junior_suitable=False,
            languages_required=["English"], contract_type="permanent", salary=None,
            summary="Lead a redesign of the order pipeline across distributed services."),
    ),
    dict(
        days_ago=3, company="Fourth Coffee", title="Fullstack Developer",
        location="Porto, Portugal", seniority="Entry level",
        employment_type="Full-time",
        html="""<p>Join our Porto office. This is an on-site role, five days a week,
        based in our Porto engineering centre.</p><ul>
        <li>React and .NET.</li><li>Azure.</li></ul>""",
        llm=dict(
            work_mode="onsite", work_mode_evidence="on-site role, five days a week",
            remote_scope="unclear", remote_countries=[], onsite_days_per_week=5,
            years_required=2, years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["English"], contract_type="permanent", salary=None,
            summary="On-site fullstack role in Porto working in React and .NET on Azure."),
    ),
    dict(
        days_ago=4, company="Alpine Ski House", title="Junior Softwareentwickler",
        location="Munich, Germany", seniority="Entry level",
        employment_type="Full-time",
        html="""<p>Wir suchen einen Junior Softwareentwickler fuer unser Team in
        Muenchen. Sehr gute Deutschkenntnisse sind erforderlich.</p>
        <p>Hybrid, drei Tage pro Woche im Buero.</p>""",
        llm=dict(
            work_mode="hybrid", work_mode_evidence="drei Tage pro Woche im Buero",
            remote_scope="unclear", remote_countries=[], onsite_days_per_week=3,
            years_required=1, years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["German"], contract_type="permanent", salary=None,
            summary="Junior development role in Munich requiring fluent German."),
    ),
    dict(
        days_ago=6, company="Wide World Importers", title="Junior Developer",
        location="Budapest, Hungary", seniority="Entry level",
        employment_type="Full-time",
        # The posting the README describes: a hybrid role abroad that used to
        # score 81 by falling back to unknown_city instead of abroad.
        html="""<p>Join our Budapest team. Hybrid working, two days a week in our
        city centre office.</p><ul><li>C# and .NET.</li>
        <li>SQL Server.</li></ul>""",
        llm=dict(
            work_mode="hybrid",
            work_mode_evidence="two days a week in our city centre office",
            remote_scope="unclear", remote_countries=[], onsite_days_per_week=2,
            years_required=1, years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["English"], contract_type="permanent", salary=None,
            summary="Hybrid junior .NET role based in Budapest."),
    ),
    dict(
        days_ago=7, company="Humongous Insurance", title="Junior Developer",
        location="Lisbon, Portugal", seniority="Entry level",
        employment_type="Full-time",
        html="""<p>Four days a week in our Lisbon office, one from home.</p>
        <ul><li>C#, .NET Framework.</li><li>A large legacy estate.</li></ul>""",
        llm=dict(
            work_mode="hybrid", work_mode_evidence="Four days a week in our Lisbon office",
            remote_scope="unclear", remote_countries=[], onsite_days_per_week=4,
            years_required=2, years_are_hard_requirement=False, junior_suitable=True,
            languages_required=["English"], contract_type="permanent", salary=None,
            summary="Maintain a large legacy .NET estate from the Lisbon office."),
    ),
    dict(
        days_ago=10, company="VanArsdel", title="Software Architect",
        location="Remote, Portugal", seniority="Mid-Senior level",
        employment_type="Full-time",
        html="""<p>Own the technical direction of our platform.</p>
        <p>Minimum of 10 years in software engineering.</p>""",
        llm=None,
    ),
]
