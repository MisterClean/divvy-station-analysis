-- Baseline files are saved from the unmodified pipeline before changing its rules.
-- Join the original observation grain, never a reused station ID by itself.
WITH links AS (
    SELECT old.station_key AS old_station_key, new.station_key AS new_station_key,
           sum(old.valid_endpoint_count::BIGINT) AS shared_valid_endpoints
    FROM read_csv('data/interim/baseline_station_observations.csv', all_varchar=true, sample_size=-1) old
    JOIN station_observations new
      ON old.archive = new.archive AND old.member = new.member AND old.role = new.role
     AND old.station_id IS NOT DISTINCT FROM new.station_id
     AND old.original_station_name IS NOT DISTINCT FROM new.original_station_name
    GROUP BY old.station_key, new.station_key
)
SELECT old.station_key AS old_station_key, old.station_name AS old_station_name,
       old.station_type AS old_station_type,
       old.station_first_trip_at AS old_first_trip_at,
       old.station_lat AS old_lat, old.station_lon AS old_lon,
       links.new_station_key, new.station_name AS new_station_name,
       new.station_first_trip_at AS new_first_trip_at,
       new.station_lat AS new_lat, new.station_lon AS new_lon,
       new.in_chicago AS new_in_chicago, new.station_type AS new_station_type,
       shared_valid_endpoints,
       CASE WHEN new.station_lat IS NULL THEN 'retained_unlocated'
            WHEN new.station_type = 'operational' THEN 'excluded_operational'
            WHEN new.in_chicago IS FALSE THEN 'outside_chicago'
            WHEN old.station_key = new.station_key THEN 'same_key'
            ELSE 'reassigned_or_rekeyed' END AS disposition
FROM read_csv('data/interim/baseline_stations.csv', all_varchar=true, sample_size=-1) old
JOIN links ON old.station_key = links.old_station_key
JOIN stations_all new ON new.station_key = links.new_station_key
ORDER BY old.station_key, links.new_station_key;
