create table product
(		
id int IDENTITY(1,1) primary key,
productname nvarchar(50) not null,
size nvarchar(20),
price numeric(5,2) not null,
ListingStatus bit default 1)   --zeng finish
