ALTER TABLE jobs ADD COLUMN extractor_version_valid INTEGER GENERATED ALWAYS AS (
    CASE
        WHEN length(extractor_version) BETWEEN 1 AND 32
         AND substr(extractor_version, 1, 1) GLOB '[A-Za-z0-9]'
         AND extractor_version NOT GLOB '*[^A-Za-z0-9._-]*'
        THEN 1 ELSE 0
    END
) VIRTUAL CHECK (extractor_version_valid = 1);
