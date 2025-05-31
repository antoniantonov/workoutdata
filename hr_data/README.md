# DuckDB queries
1. Output the maximum and average heart rate, calories burned, duration, and notes for each workout, ordered by calories burned in descending order.
```
SELECT
  hr_stats.workoutId,
  MAX(hr_stats."HR (bpm)") AS max_hr,
  ROUND(AVG(hr_stats."HR (bpm)"), 1) AS avg_hr,
  meta.Calories,
  meta.duration,
  notes.Notes
FROM
  timeseries AS hr_stats
JOIN
  workout_metadata AS meta
  ON hr_stats.workoutId = meta.workoutId
JOIN
  workout_metadata AS notes
  ON hr_stats.workoutId = notes.workoutId
GROUP BY
  hr_stats.workoutId, meta.Calories, meta.duration, notes.Notes
ORDER BY
  meta.Calories DESC;
  ```

  2. Output the maximum and average heart rate, calories burned, duration for each workout, ordered by calories burned in descending order.
  ```
  SELECT
  hr_stats.workoutId,
  MAX(hr_stats."HR (bpm)") AS max_hr,
  ROUND(AVG(hr_stats."HR (bpm)"), 1) AS avg_hr,
  meta.Calories,
  meta.duration
FROM
  timeseries AS hr_stats
JOIN
  workout_metadata AS meta
ON
  hr_stats.workoutId = meta.workoutId
GROUP BY
  hr_stats.workoutId, meta.Calories, meta.duration
ORDER BY
  meta.Calories DESC;
  ```
