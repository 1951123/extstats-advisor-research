-- rq5-tiny-cost-fixture-v1-catalog-corrected-v2; execute only in an explicitly owned empty template database.
CREATE TABLE public.rq5_tiny_cost_fixture (id integer NOT NULL, region text NOT NULL, tier text NOT NULL, segment text NOT NULL);
INSERT INTO public.rq5_tiny_cost_fixture (id, region, tier, segment) VALUES
  (1, 'north', 'gold', 'enterprise'),
  (2, 'north', 'gold', 'enterprise'),
  (3, 'north', 'gold', 'small'),
  (4, 'north', 'silver', 'small'),
  (5, 'south', 'silver', 'small'),
  (6, 'south', 'silver', 'small'),
  (7, 'south', 'silver', 'enterprise'),
  (8, 'south', 'bronze', 'small'),
  (9, 'west', 'bronze', 'small'),
  (10, 'west', 'bronze', 'enterprise'),
  (11, 'west', 'gold', 'enterprise'),
  (12, 'west', 'gold', 'enterprise');
ANALYZE public.rq5_tiny_cost_fixture;
