-- Run against data/stations.duckdb. station_key is the resolved entity, not a raw source ID.
-- Each observation includes only the minimum timestamp of a valid trip (end >= start).
WITH first_observed AS (
    SELECT station_key, min(first_at) AS station_first_trip_at
    FROM station_observations
    GROUP BY station_key
)
SELECT s.station_key, s.station_id, s.station_name, s.station_lon, s.station_lat,
       f.station_first_trip_at, s.quality_flags
FROM stations AS s
JOIN first_observed AS f USING (station_key)
ORDER BY station_first_trip_at, station_key;
