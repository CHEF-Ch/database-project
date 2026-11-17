/* Existing v1.0 installations: enable read-only customer account ledgers.
   Member ownership remains enforced by the application WHERE member_id = ?.
   No business data or schema changes. */
USE MilkTeaShop_v01;
GO
GRANT SELECT ON dbo.member_point_log TO role_customer;
GRANT SELECT ON dbo.member_balance_log TO role_customer;
GO
