CREATE VIEW IF NOT EXISTS analytics_incidents AS
SELECT
    number AS incident_number,

    created_at,
    target_date,
    plan_finish_date,
    close_date,
    detection_time,
    start_time,
    end_time,

    system_name,
    work_group,
    element_name,
    created_by,
    executor_name,

    status,
    priority_code,
    resolution_code,
    registration_basis,
    inc_type,
    stand,

    impact_custom_service,
    no_impact,
    is_root,

    mttd,
    mttr,
    downtime,

    description,
    resolution_description,
    reason_inc,
    solution,
    impact,
    ai_description,

    updated_at
FROM incidents;


CREATE VIEW IF NOT EXISTS analytics_assignments AS
SELECT
    id AS assignment_id,
    incident_id,
    ior,

    task,
    unit,
    assignment,
    responsible,

    deadline,
    assigned_at,
    status,

    created_at,
    updated_at
FROM assignments;