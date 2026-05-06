CREATE TABLE IF NOT EXISTS data_sources (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_key VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    mode ENUM('html', 'browser') NOT NULL DEFAULT 'html',
    bootstrap_depth ENUM('full') NOT NULL DEFAULT 'full',
    weekly_depth ENUM('first_page') NOT NULL DEFAULT 'first_page',
    allowed_attachments_json JSON NULL,
    enable_ai_link_classifier TINYINT(1) NOT NULL DEFAULT 0,
    enable_ai_content_judge TINYINT(1) NOT NULL DEFAULT 0,
    stop_when_older_than_days INT NOT NULL DEFAULT 730,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    bootstrap_completed TINYINT(1) NOT NULL DEFAULT 0,
    last_seen_published_at DATETIME NULL,
    last_seen_url VARCHAR(1024) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_entry_urls (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_id BIGINT NOT NULL,
    entry_url VARCHAR(1024) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_source_entry_url (source_id, entry_url(255)),
    CONSTRAINT fk_entry_source FOREIGN KEY (source_id) REFERENCES data_sources(id)
);

CREATE TABLE IF NOT EXISTS crawl_candidates (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_id BIGINT NOT NULL,
    entry_url VARCHAR(1024) NOT NULL,
    source_page_url VARCHAR(1024) NULL,
    page_url VARCHAR(1024) NOT NULL,
    anchor_text VARCHAR(512) NULL,
    title VARCHAR(512) NULL,
    published_at DATETIME NULL,
    status ENUM(
        'discovered',
        'prefilter_dropped',
        'fetched',
        'kept',
        'dropped',
        'duplicate_url',
        'duplicate_content',
        'duplicate_title',
        'failed'
    ) NOT NULL DEFAULT 'discovered',
    doc_type VARCHAR(64) NULL,
    decision_reason TEXT NULL,
    reason_tags JSON NULL,
    canonical_doc_id BIGINT NULL,
    fetched_at DATETIME NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_candidate_page_url (page_url(255)),
    KEY idx_candidate_source_status (source_id, status),
    CONSTRAINT fk_candidate_source FOREIGN KEY (source_id) REFERENCES data_sources(id)
);

CREATE TABLE IF NOT EXISTS raw_documents (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_id BIGINT NOT NULL,
    candidate_id BIGINT NULL,
    entry_url VARCHAR(1024) NOT NULL,
    page_url VARCHAR(1024) NOT NULL,
    title VARCHAR(512) NOT NULL,
    content LONGTEXT NOT NULL,
    published_at DATETIME NULL,
    crawled_at DATETIME NOT NULL,
    doc_type VARCHAR(64) NULL,
    content_hash CHAR(64) NOT NULL,
    title_norm VARCHAR(512) NOT NULL,
    reason_tags JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_document_page_url (page_url(255)),
    UNIQUE KEY uq_document_hash (content_hash),
    KEY idx_document_source_time (source_id, published_at),
    KEY idx_document_title_norm (title_norm(255)),
    CONSTRAINT fk_document_source FOREIGN KEY (source_id) REFERENCES data_sources(id),
    CONSTRAINT fk_document_candidate FOREIGN KEY (candidate_id) REFERENCES crawl_candidates(id)
);

CREATE TABLE IF NOT EXISTS raw_attachments (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    raw_document_id BIGINT NOT NULL,
    attachment_url VARCHAR(1024) NOT NULL,
    file_name VARCHAR(255) NULL,
    file_ext VARCHAR(16) NULL,
    content_text MEDIUMTEXT NULL,
    content_hash CHAR(64) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_attachment_url (attachment_url(255)),
    CONSTRAINT fk_attachment_document FOREIGN KEY (raw_document_id) REFERENCES raw_documents(id)
);

CREATE TABLE IF NOT EXISTS extracted_clues (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    raw_document_id BIGINT NOT NULL,
    source_id BIGINT NOT NULL,
    status ENUM('kept', 'dropped', 'failed') NOT NULL DEFAULT 'kept',
    clue_type VARCHAR(64) NULL,
    investment_relevance VARCHAR(32) NULL,
    relevance_score INT NULL,
    core_technology TEXT NULL,
    application_scenarios JSON NULL,
    transformation_signals JSON NULL,
    summary TEXT NULL,
    decision_reason TEXT NULL,
    reason_tags JSON NULL,
    used_ai TINYINT(1) NOT NULL DEFAULT 1,
    model_name VARCHAR(128) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_extracted_raw_document (raw_document_id),
    KEY idx_extracted_source_status (source_id, status),
    KEY idx_extracted_relevance (investment_relevance, relevance_score),
    CONSTRAINT fk_extracted_document FOREIGN KEY (raw_document_id) REFERENCES raw_documents(id),
    CONSTRAINT fk_extracted_source FOREIGN KEY (source_id) REFERENCES data_sources(id)
);

CREATE TABLE IF NOT EXISTS clue_pool (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    extracted_clue_id BIGINT NOT NULL,
    raw_document_id BIGINT NOT NULL,
    source_id BIGINT NOT NULL,
    title VARCHAR(512) NOT NULL,
    page_url VARCHAR(1024) NOT NULL,
    clue_type VARCHAR(64) NULL,
    investment_relevance VARCHAR(32) NULL,
    relevance_score INT NULL,
    core_technology TEXT NULL,
    summary TEXT NULL,
    published_at DATETIME NULL,
    pool_status ENUM('pending', 'confirmed', 'ignored') NOT NULL DEFAULT 'pending',
    reviewer VARCHAR(128) NULL,
    review_note TEXT NULL,
    pushed_at DATETIME NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_pool_extracted_clue (extracted_clue_id),
    UNIQUE KEY uq_pool_raw_document (raw_document_id),
    KEY idx_pool_source_status (source_id, pool_status),
    KEY idx_pool_relevance (investment_relevance, relevance_score),
    CONSTRAINT fk_pool_extracted_clue FOREIGN KEY (extracted_clue_id) REFERENCES extracted_clues(id),
    CONSTRAINT fk_pool_raw_document FOREIGN KEY (raw_document_id) REFERENCES raw_documents(id),
    CONSTRAINT fk_pool_source FOREIGN KEY (source_id) REFERENCES data_sources(id)
);

CREATE TABLE IF NOT EXISTS job_runs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    job_type VARCHAR(64) NOT NULL,
    source_key VARCHAR(64) NULL,
    trigger_mode ENUM('manual', 'scheduled', 'admin') NOT NULL DEFAULT 'manual',
    status ENUM('running', 'succeeded', 'failed') NOT NULL DEFAULT 'running',
    summary_json JSON NULL,
    error_message TEXT NULL,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_job_runs_status (status, started_at),
    KEY idx_job_runs_type (job_type, started_at),
    KEY idx_job_runs_source (source_key, started_at)
);

CREATE TABLE IF NOT EXISTS crawl_failures (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_id BIGINT NOT NULL,
    page_url VARCHAR(1024) NOT NULL,
    error_stage VARCHAR(64) NOT NULL,
    error_message TEXT NOT NULL,
    retry_count INT NOT NULL DEFAULT 0,
    status ENUM('pending', 'resolved', 'dropped') NOT NULL DEFAULT 'pending',
    last_error_at DATETIME NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_failure_status (status, retry_count),
    CONSTRAINT fk_failure_source FOREIGN KEY (source_id) REFERENCES data_sources(id)
);
