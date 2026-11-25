/* 独立分类样例。在 @requested 中填写一个或多个待查询分类。 */
SET NOCOUNT ON;

DECLARE @category TABLE (
    category_id INT PRIMARY KEY,
    parent_id INT NULL,
    category_name NVARCHAR(40) NOT NULL
);
INSERT @category VALUES
    (1, NULL, N'饮品'), (2, 1, N'奶茶'), (3, 1, N'鲜茶'),
    (4, 2, N'经典奶茶'), (5, 2, N'鲜奶茶'), (6, 3, N'乌龙茶');

DECLARE @requested TABLE (category_id INT PRIMARY KEY);
INSERT @requested VALUES (1), (2), (4), (999);

;WITH category_tree AS (
    SELECT r.category_id AS requested_id, c.category_id, c.parent_id, c.category_name,
           0 AS depth, CAST(c.category_name AS NVARCHAR(4000)) AS category_path,
           CAST('/' + CONVERT(VARCHAR(11), c.category_id) + '/' AS VARCHAR(4000)) AS visited
    FROM @requested AS r
    JOIN @category AS c ON c.category_id = r.category_id
    UNION ALL
    SELECT t.requested_id, c.category_id, c.parent_id, c.category_name,
           t.depth + 1, CAST(CONCAT(t.category_path, N' / ', c.category_name) AS NVARCHAR(4000)),
           CAST(CONCAT(t.visited, c.category_id, '/') AS VARCHAR(4000))
    FROM category_tree AS t
    JOIN @category AS c ON c.parent_id = t.category_id
    WHERE CHARINDEX('/' + CONVERT(VARCHAR(11), c.category_id) + '/', t.visited) = 0
)
SELECT r.category_id AS requested_id, t.category_id, t.parent_id,
       t.category_name, t.depth, t.category_path
FROM @requested AS r
LEFT JOIN category_tree AS t ON t.requested_id = r.category_id
ORDER BY r.category_id, t.depth, t.category_id
OPTION (MAXRECURSION 100);
